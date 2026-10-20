"""Recomendacion de equipos reales (Andres, LEV-2026-0008): "deberia dar una recomendacion de cuales camaras podria
utilizar con base en los requerimientos que le estoy dando, ligado a Productos y Servicios".

La sugerencia de siempre (lineas genericas) no cambia; con ?candidates=true cada linea trae hasta 3 productos reales,
ordenados por requisitos, existencias (propias primero) y precio, y "Usar este" deja la linea con su producto, de
modo que el costeo toma el costo del producto elegido en vez de quedar "sin costo"."""

from decimal import Decimal

import pytest

from app.models import Product, Survey
from app.services import inventory as invsvc
from app.services import supplier_catalog
from app.services.survey_specs import suggest_materials
from tests.test_campo import _login
from tests.test_campo_p1 import _plain
from tests.test_comercial2 import _cust

FX = Decimal("512.35")  # tipo de cambio sembrado (seeds)

# codigo, nombre, costo USD, precio CRC, existencias propias, existencias del proveedor (item vinculado del catalogo)
CATALOGO = [
    ("T-CAM-B4-EXT", "Cámara IP Bullet 4MP exterior PoE IR 30m IP67", 40, 45000, 12, None),
    ("T-CAM-B4-PROV", "Cámara IP Bullet 4MP exterior PoE ColorVu IP67", 35, 40000, 0, 30),
    ("T-CAM-B4-INT", "Cámara IP Bullet 4MP interior PoE", 30, 35000, 20, None),
    ("T-CAM-B2-EXT", "Cámara IP Bullet 2MP exterior PoE IR", 20, 25000, 50, None),
    ("T-CAM-D4-INT", "Cámara IP Domo 4MP interior PoE IR", 38, 42000, 10, None),
    ("T-CAM-D4-EXT", "Cámara IP Domo 4MP exterior PoE IP67", 45, 50000, 0, None),
    ("T-NVR-8", "NVR 8 canales 4K PoE /8P", 120, 150000, 3, None),
    ("T-NVR-16", "NVR 16 canales 4K", 160, 200000, 2, None),
    ("T-HDD-4", "Disco duro 4TB Purple videovigilancia", 90, 110000, 6, None),
    ("T-HDD-6", "Disco duro 6TB Purple videovigilancia", 130, 160000, 4, None),
    ("T-SW-8", "Switch PoE 8 puertos 120W", 60, 75000, 5, None),
    ("T-SW-16", "Switch PoE 16 puertos 250W", 140, 180000, 2, None),
    ("T-UTP-305", "Bobina cable UTP Cat6 305 m exterior", 150, 190000, 2, None),
]


def _catalogo(db, tid) -> dict[str, Product]:
    w = invsvc.default_warehouse(db, tid)
    out = {}
    for code, name, cost, price, stock, _prov in CATALOGO:
        p = Product(tenant_id=tid, code=code, name=name, price=price, cost=cost, cost_currency="USD", unit="Unid")
        db.add(p)
        db.flush()
        if stock:
            invsvc.move(db, tid, None, p.id, w.id, "entrada", Decimal(stock), "INV-PRUEBA")
        out[code] = p
    db.commit()
    from tests.test_disponibilidad import supplier_item

    for code, *_rest, prov in CATALOGO:
        if prov:
            # la existencia del proveedor solo cuenta vinculada y con fecha (services/availability)
            supplier_item(db, out[code].id, prov, cost=out[code].cost)
    return out


def _puntos():
    ext = [
        {
            "code": f"CAM-0{i}",
            "label": f"Patio {i}",
            "data": {"tipo": "Bullet", "resolucion": "4 MP", "ambiente": "Exterior", "alimentacion": "PoE", "distancia_m": 30},
        }
        for i in range(1, 5)
    ]
    inte = [
        {
            "code": f"CAM-0{i}",
            "label": f"Oficina {i}",
            "data": {"tipo": "Domo", "resolucion": "4 MP", "ambiente": "Interior", "alimentacion": "PoE", "distancia_m": 20},
        }
        for i in range(5, 9)
    ]
    return ext + inte


def _levantamiento(client, headers=None, **extra):
    body = {
        "kind": "cctv",
        "customer_id": _cust(client)["id"],
        "points": _puntos(),
        "common": {"cctv_sistema": {"dias_retencion": 30, "bitrate_mbps": 4}},
        **extra,
    }
    r = client.post("/surveys", json=body, headers=headers or {})
    assert r.status_code == 201, r.text
    return r.json()


def _by(lines):
    return {x["name"]: x for x in lines}


def _reco(client, sid, headers=None):
    r = client.post(f"/surveys/{sid}/suggest?candidates=true", headers=headers or {})
    assert r.status_code == 200, r.text
    return _by(r.json())


@pytest.fixture
def cat(client, auth, db_session):
    return _catalogo(db_session, auth["tenant"]["id"])


# ---------- la sugerencia de siempre no cambia ----------
def test_sugerencia_sin_candidatos_queda_igual(client, auth, cat, db_session):
    s = _levantamiento(client)
    plain = client.post(f"/surveys/{s['id']}/suggest").json()
    assert all("candidates" not in x and "recommend" not in x for x in plain)
    assert not any(x["name"].startswith("Disco duro") for x in plain)
    # la API devuelve exactamente lo que calcula survey_specs (el formato de siempre)
    sv = db_session.get(Survey, s["id"])
    assert _plain(plain) == _plain(suggest_materials("cctv", list(sv.points), list(sv.items), common=sv.common))
    # con candidatos, cada linea de siempre trae lo mismo y solo suma campos (y la linea extra de discos)
    rich = client.post(f"/surveys/{s['id']}/suggest?candidates=true").json()
    base = [{k: v for k, v in x.items() if k not in ("candidates", "recommend")} for x in rich if x.get("origin") != "recomendacion"]
    assert _plain(base) == _plain(plain)
    assert [x["name"] for x in rich if x.get("origin") == "recomendacion"] == ["Disco duro de videovigilancia"]


# ---------- candidatos correctos y ordenados ----------
def test_candidatos_por_linea_ordenados_por_requisitos_y_existencias(client, auth, cat):
    s = _levantamiento(client)
    lines = _reco(client, s["id"])

    bullet = lines["Cámara Bullet 4 MP"]
    assert bullet["quantity"] == 4 and bullet["recommend"]["needs"] == ["Bullet", "Exterior", "4 MP", "PoE"]
    nombres = [c["name"] for c in bullet["candidates"]]
    # solo bullets de exterior de 4 MP con PoE: la interior, la de 2 MP y las domo no cumplen
    assert nombres == ["Cámara IP Bullet 4MP exterior PoE IR 30m IP67", "Cámara IP Bullet 4MP exterior PoE ColorVu IP67"]
    top, prov = bullet["candidates"]
    assert top["recommended"] is True and prov["recommended"] is False
    assert top["availability"] == "bodega" and top["availability_label"] == "12 en bodega"
    assert prov["availability"] == "proveedor"  # la mas barata, pero solo con existencias del proveedor: va despues
    assert top["score"] > prov["score"] and top["meets"] and prov["meets"]
    assert top["reason"].startswith("Bullet, Exterior, 4 MP, PoE · 12 en bodega · costo ₡")
    assert Decimal(str(top["cost_crc"])) == (Decimal(40) * FX).quantize(Decimal("0.01"))
    assert top["quantity"] == 4 and top["product_id"] == cat["T-CAM-B4-EXT"].id
    assert any(c["label"] == "Cumple los requisitos" and c["points"] == 1000 for c in top["criteria"])

    domo = lines["Cámara Domo 4 MP"]
    assert all("domo" in c["name"].lower() for c in domo["candidates"]) and len(domo["candidates"]) == 3
    assert domo["candidates"][0]["name"] == "Cámara IP Domo 4MP interior PoE IR" and domo["candidates"][0]["recommended"]
    # la domo de exterior sin existencias queda de ultima aunque cumpla
    assert domo["candidates"][-1]["name"] == "Cámara IP Domo 4MP exterior PoE IP67" and domo["candidates"][-1]["availability"] == "sin_existencias"

    nvr = lines["NVR 8 canales"]["candidates"]
    assert nvr[0]["name"] == "NVR 8 canales 4K PoE /8P" and nvr[0]["recommended"]
    assert "PoE integrado" in nvr[0]["matches"] and all(c["meets"] for c in nvr)
    assert "NVR 16 canales 4K" in [c["name"] for c in nvr]  # cumple (sobran canales) pero va despues

    sw = lines["Switch PoE 16 puertos"]["candidates"]
    assert [c["name"] for c in sw] == ["Switch PoE 16 puertos 250W"]  # los de 8 puertos no alcanzan

    disco = lines["Disco duro de videovigilancia"]
    assert disco["origin"] == "recomendacion" and disco["apply_generic"] is False and disco["unit"] == "TB"
    assert Decimal(str(disco["quantity"])) == Decimal("10.368")  # 8 x 4 Mb/s x 86 400 x 30 / 8 000 000
    d0 = disco["candidates"][0]
    assert d0["name"] == "Disco duro 6TB Purple videovigilancia" and d0["quantity"] == 2 and d0["recommended"]
    assert d0["quantity_note"] == "10,4 TB → 2 × 6 TB = 12 TB"
    assert disco["candidates"][1]["quantity"] == 3  # 3 discos de 4 TB cuestan mas que 2 de 6 TB

    cable = lines["Cable (metros con 15 % de holgura)"]
    c0 = cable["candidates"][0]
    assert cable["quantity"] == 230 and c0["quantity"] == 1 and c0["unit"] == "Unid" and c0["quantity_note"] == "230 m → 1 × 305 m"

    # lineas sin equipo del catalogo: se dice, no se inventa
    assert lines["Conectores RJ45"]["candidates"] == [] and lines["Conectores RJ45"]["recommend"]["note"]


def test_ninguno_cumple_se_muestra_el_mas_cercano_sin_recomendar(client, auth, cat, db_session):
    cat["T-SW-16"].active = False  # el unico de 16 puertos sale del catalogo
    db_session.commit()
    s = _levantamiento(client)
    sw = _reco(client, s["id"])["Switch PoE 16 puertos"]
    assert sw["candidates"] and not any(c["recommended"] for c in sw["candidates"])
    assert all(c["meets"] is False and c["fails"] for c in sw["candidates"])
    assert "Ningún equipo" in sw["recommend"]["note"]


# ---------- usar este -> linea con producto -> costeo con costo ----------
def test_usar_este_crea_la_linea_y_el_costeo_toma_el_costo_del_producto(client, auth, cat):
    s = _levantamiento(client)
    lines = _reco(client, s["id"])
    top = lines["Cámara Bullet 4 MP"]["candidates"][0]
    r = client.post(f"/surveys/{s['id']}/suggest/apply-product", json={"line": "Cámara Bullet 4 MP", "product_id": top["product_id"], "quantity": 4})
    assert r.status_code == 200, r.text
    it = r.json()["item"]
    assert r.json()["created"] is True
    assert it["product_id"] == cat["T-CAM-B4-EXT"].id and it["name"] == "Cámara IP Bullet 4MP exterior PoE IR 30m IP67" and it["kind"] == "equipo"
    assert it["recommended_for"] == "Cámara Bullet 4 MP" and Decimal(str(it["quantity"])) == 4

    # la bobina cubre los 230 m del cable
    client.post(
        f"/surveys/{s['id']}/suggest/apply-product", json={"line": "Cable (metros con 15 % de holgura)", "product_id": cat["T-UTP-305"].id, "quantity": 1}
    )
    # la sugerencia de siempre ya la ve cubierta (no la propone de nuevo ni duplica)
    plain = _by(client.post(f"/surveys/{s['id']}/suggest").json())
    assert plain["Cámara Bullet 4 MP"]["action"] == "cubierto" and plain["Cámara Bullet 4 MP"]["matched_name"] == it["name"]
    assert plain["Cable (metros con 15 % de holgura)"]["action"] == "cubierto"
    assert Decimal(str(plain["Cable (metros con 15 % de holgura)"]["existing_quantity"])) == 305
    # y la recomendacion marca el equipo en uso
    again = _reco(client, s["id"])["Cámara Bullet 4 MP"]
    assert again["recommend"]["current"]["product_id"] == top["product_id"] and again["candidates"][0]["selected"] is True

    # elegir otro equipo reemplaza la misma linea (no duplica)
    prov = again["candidates"][1]
    r2 = client.post(f"/surveys/{s['id']}/suggest/apply-product", json={"line": "Cámara Bullet 4 MP", "product_id": prov["product_id"], "quantity": 4})
    assert r2.json()["created"] is False and r2.json()["item"]["id"] == it["id"]
    items = r2.json()["survey"]["items"]
    assert len([x for x in items if x.get("recommended_for") == "Cámara Bullet 4 MP"]) == 1

    cost = client.get(f"/surveys/{s['id']}/costing").json()
    cam = next(x for x in cost["lines"] if x["item_id"] == it["id"])
    assert cam["has_cost"] and cam["cost_source"] == "catalogo"
    assert Decimal(str(cam["unit_cost"])) == (Decimal(35) * FX).quantize(Decimal("0.01"))
    assert cam["name"] not in cost["missing_cost"]


def test_la_linea_generica_ya_cargada_se_completa_con_el_producto(client, auth, cat):
    """Lo de siempre: primero se agregan las lineas genericas; despues "Usar este" les pone el producto ahi mismo."""
    s = _levantamiento(client)
    sug = client.post(f"/surveys/{s['id']}/suggest").json()
    picks = [{"name": x["name"], "quantity": x["add_quantity"], "unit": x["unit"], "kind": x["kind"]} for x in sug if x["action"] == "nuevo"]
    full = client.post(f"/surveys/{s['id']}/suggest/apply", json=picks).json()["survey"]
    gen = next(i for i in full["items"] if i["name"] == "NVR 8 canales")
    assert gen["product_id"] is None
    # sin producto el costeo la deja sin costo (el problema de Andres)
    assert "NVR 8 canales" in client.get(f"/surveys/{s['id']}/costing").json()["missing_cost"]

    nvr = _reco(client, s["id"])["NVR 8 canales"]
    assert nvr["item_id"] == gen["id"] and nvr["action"] == "cubierto"
    r = client.post(
        f"/surveys/{s['id']}/suggest/apply-product",
        json={"line": "NVR 8 canales", "product_id": nvr["candidates"][0]["product_id"], "quantity": 1, "item_id": gen["id"]},
    )
    assert r.status_code == 200 and r.json()["created"] is False and r.json()["item"]["id"] == gen["id"]
    cost = client.get(f"/surveys/{s['id']}/costing").json()
    ln = next(x for x in cost["lines"] if x["item_id"] == gen["id"])
    assert ln["name"] == "NVR 8 canales 4K PoE /8P" and ln["has_cost"] and Decimal(str(ln["unit_cost"])) == (Decimal(120) * FX).quantize(Decimal("0.01"))
    assert "NVR 8 canales" not in cost["missing_cost"]
    # el guardado del portal (que no conoce la recomendacion) no la borra
    body = {"kind": "cctv", "customer_id": full["customer_id"], "points": full["points"], "items": r.json()["survey"]["items"], "common": full["common"]}
    saved = client.put(f"/surveys/{s['id']}", json=body).json()
    assert next(i for i in saved["items"] if i["id"] == gen["id"] or i["name"] == "NVR 8 canales 4K PoE /8P")["recommended_for"] == "NVR 8 canales"
    assert client.post(f"/surveys/{s['id']}/quote", json={"skip_review": True}).status_code == 201


def test_disco_por_tb_y_sumar_en_unidades_del_equipo(client, auth, cat):
    s = _levantamiento(client)
    d = _reco(client, s["id"])[("Disco duro de videovigilancia")]
    r = client.post(f"/surveys/{s['id']}/suggest/apply-product", json={"line": d["name"], "product_id": d["candidates"][0]["product_id"], "quantity": 2})
    assert r.status_code == 200, r.text
    again = _reco(client, s["id"])["Disco duro de videovigilancia"]
    assert again["action"] == "cubierto" and Decimal(str(again["existing_quantity"])) == 12  # 2 discos x 6 TB
    # 60 dias de retencion: faltan 8,7 TB; sumarlos agrega discos (2 de 6 TB), no 8,7 "unidades"
    full = r.json()["survey"]
    body = {
        "kind": "cctv",
        "customer_id": full["customer_id"],
        "points": full["points"],
        "items": full["items"],
        "common": {"cctv_sistema": {"dias_retencion": 60, "bitrate_mbps": 4}},
    }
    assert client.put(f"/surveys/{s['id']}", json=body).status_code == 200
    mas = _reco(client, s["id"])["Disco duro de videovigilancia"]
    assert mas["action"] == "sumar" and Decimal(str(mas["add_quantity"])) == Decimal("8.736")
    out = client.post(
        f"/surveys/{s['id']}/suggest/apply", json=[{"name": mas["name"], "quantity": mas["add_quantity"], "unit": "TB", "item_id": mas["item_id"]}]
    ).json()
    disco = next(i for i in out["survey"]["items"] if i.get("recommended_for") == "Disco duro de videovigilancia")
    assert Decimal(str(disco["quantity"])) == 4 and disco["unit"] == "Unid"


def test_guardar_dos_veces_sin_recargar_no_pierde_costo_ni_equipo(client, auth, cat):
    """Cada guardado renumera las lineas; el portal guardaba otra vez con los ids viejos (Costear y mover el margen)
    y se perdia el costo escrito a mano. Ahora la misma linea se reconoce por producto y nombre."""
    s = _levantamiento(client, items=[{"name": "Gabinete 15U", "quantity": 1}])
    gab = next(i for i in s["items"] if i["name"] == "Gabinete 15U")
    client.post(f"/surveys/{s['id']}/costs", json=[{"item_id": gab["id"], "unit_cost": 50000}])
    full = client.post(f"/surveys/{s['id']}/suggest/apply-product", json={"line": "NVR 8 canales", "product_id": cat["T-NVR-8"].id, "quantity": 1}).json()[
        "survey"
    ]
    body = {"kind": "cctv", "customer_id": full["customer_id"], "points": full["points"], "items": full["items"], "common": full["common"]}
    client.put(f"/surveys/{s['id']}", json=body)  # primer guardado: ids al dia
    again = client.put(f"/surveys/{s['id']}", json=body).json()  # segundo con los mismos ids (ya viejos)
    assert next(i for i in again["items"] if i["product_id"] == cat["T-NVR-8"].id)["recommended_for"] == "NVR 8 canales"
    cost = client.get(f"/surveys/{s['id']}/costing").json()
    g = next(x for x in cost["lines"] if x["name"] == "Gabinete 15U")
    assert g["cost_source"] == "levantamiento" and Decimal(str(g["unit_cost"])) == 50000


# ---------- oferta solo de proveedor (catalogo Eurocomp) ----------
OFERTA = {
    "source": "proveedor",
    "product_id": None,
    "supplier_item_id": 9001,
    "supplier": "Eurocomp",
    "code": "DS-2CD1143G2-LIU",
    "name": "Cámara domo 4MP interior PoE IR Hikvision",
    "model": "DS-2CD1143G2-LIU",
    "cost": Decimal("33.50"),
    "currency": "USD",
    "cost_crc": None,
    "sale_price": None,
    "stock_propio": 0,
    "stock_proveedor": 25,
    "updated_at": None,
}


def test_oferta_de_proveedor_se_elige_y_se_costea_con_el_tipo_de_cambio(client, auth, cat, monkeypatch):
    real = supplier_catalog.search_offers

    def con_proveedor(db, tenant_id, *, terms=(), category=None, attrs=None, limit=10):
        out = real(db, tenant_id, terms=terms, category=category, attrs=attrs, limit=limit)
        if any(t in ("domo", "camara", "DS-2CD1143G2-LIU") for t in terms):
            out = out + [dict(OFERTA)]
        return out

    monkeypatch.setattr(supplier_catalog, "search_offers", con_proveedor)
    s = _levantamiento(client)
    domo = _reco(client, s["id"])["Cámara Domo 4 MP"]
    prov = next(c for c in domo["candidates"] if c["source"] == "proveedor")
    assert prov["availability_label"] == "Eurocomp: 25 disponibles" and prov["meets"]
    assert domo["candidates"][0]["source"] == "propio"  # primero lo que hay en bodega
    assert prov["sale_price"] and prov["price_estimated"] is True  # precio de venta estimado con el margen

    r = client.post(
        f"/surveys/{s['id']}/suggest/apply-product",
        json={"line": "Cámara Domo 4 MP", "supplier_item_id": 9001, "code": "DS-2CD1143G2-LIU", "quantity": 4},
    )
    assert r.status_code == 200, r.text
    it = r.json()["item"]
    assert it["product_id"] is None and it["supplier_item_id"] == 9001 and it["offer_source"] == "proveedor" and it["supplier"] == "Eurocomp"
    assert "cost" not in it and "supplier_cost" not in it  # el detalle del levantamiento no lleva costos
    cost = client.get(f"/surveys/{s['id']}/costing").json()
    ln = next(x for x in cost["lines"] if x["item_id"] == it["id"])
    assert ln["cost_source"] == "proveedor" and ln["supplier"] == "Eurocomp"
    assert Decimal(str(ln["unit_cost"])) == (Decimal("33.50") * FX).quantize(Decimal("0.01"))
    assert ln["name"] not in cost["missing_cost"]
    # una oferta que ya no existe no se puede elegir (el costo nunca viene del navegador)
    bad = client.post(f"/surveys/{s['id']}/suggest/apply-product", json={"line": "Cámara Domo 4 MP", "supplier_item_id": 777, "quantity": 4})
    assert bad.status_code == 409


# ---------- el tecnico no ve costos ----------
def test_tecnico_ve_equipo_y_existencias_sin_costos_ni_precios(client, auth, cat, db_session):
    tk = _login(client, db_session, auth["tenant"]["id"], "tec-reco@ejemplo.com", "tecnico")
    s = _levantamiento(client, headers=tk)
    lines = _reco(client, s["id"], headers=tk)
    for ln in lines.values():
        for c in ln.get("candidates") or []:
            assert not {"cost", "cost_crc", "currency", "sale_price", "price_estimated", "price_points"} & set(c)
            assert "₡" not in c["reason"] and not any("Precio" in x["label"] for x in c["criteria"])
    top = lines["Cámara Bullet 4 MP"]["candidates"][0]
    assert top["reason"] == "Bullet, Exterior, 4 MP, PoE · 12 en bodega"
    # puede elegir el equipo (no ve el costo; el costo lo pone el sistema)
    r = client.post(
        f"/surveys/{s['id']}/suggest/apply-product", json={"line": "Cámara Bullet 4 MP", "product_id": top["product_id"], "quantity": 4}, headers=tk
    )
    assert r.status_code == 200 and "unit_cost" not in r.json()["item"]
    assert client.get(f"/surveys/{s['id']}/costing", headers=tk).status_code == 403


def test_errores_de_eleccion(client, auth, cat):
    s = _levantamiento(client)
    url = f"/surveys/{s['id']}/suggest/apply-product"
    assert client.post(url, json={"line": "Cámara Bullet 4 MP", "quantity": 4}).status_code == 422  # sin producto
    assert client.post(url, json={"line": "No existe", "product_id": cat["T-NVR-8"].id, "quantity": 1}).status_code == 409
    assert client.post(url, json={"line": "Cámara Bullet 4 MP", "product_id": 999999, "quantity": 4}).status_code == 409
    assert client.post(url, json={"line": "Cámara Bullet 4 MP", "product_id": cat["T-NVR-8"].id, "quantity": 0}).status_code == 422
    assert client.post(url, json={"line": "Cámara Bullet 4 MP", "product_id": cat["T-NVR-8"].id, "quantity": 1, "item_id": 999999}).status_code == 404
    # servicios y alquileres no llevan equipo del catalogo
    lines = _reco(client, s["id"])
    assert all(not x["candidates"] for x in lines.values() if x["kind"] == "servicio")


def test_elegido_a_mano_aunque_no_sea_candidato(client, auth, cat):
    """El usuario manda: puede usar un producto que no salio entre los 3 (queda registrado con su motivo)."""
    s = _levantamiento(client)
    r = client.post(f"/surveys/{s['id']}/suggest/apply-product", json={"line": "Cámara Bullet 4 MP", "product_id": cat["T-CAM-B2-EXT"].id, "quantity": 4})
    assert r.status_code == 200
    it = r.json()["item"]
    assert it["product_id"] == cat["T-CAM-B2-EXT"].id and it["recommended_for"] == "Cámara Bullet 4 MP"


def test_si_el_catalogo_falla_la_sugerencia_y_el_costeo_siguen(client, auth, cat, db_session, monkeypatch):
    """La recomendacion nunca tumba la sugerencia de siempre (el boton "Sugerir materiales" la usa) ni el costeo."""
    from sqlalchemy import select

    from app.services import equipment_reco

    s = _levantamiento(client)
    # NVR del catalogo demo, sin costo: el costeo le busca la mejor oferta del proveedor
    demo = db_session.scalar(select(Product).where(Product.code == "NVR-8CH"))
    assert client.post(f"/surveys/{s['id']}/suggest/apply-product", json={"line": "NVR 8 canales", "product_id": demo.id, "quantity": 1}).status_code == 200

    def caido(*a, **k):
        raise RuntimeError("catalogo de proveedor caido")

    monkeypatch.setattr(supplier_catalog, "search_offers", caido)
    monkeypatch.setattr(supplier_catalog, "best_offer_for_product", caido)
    lines = _reco(client, s["id"])
    bullet = lines["Cámara Bullet 4 MP"]
    assert bullet["candidates"] == [] and "No se pudo consultar" in bullet["recommend"]["note"]
    cost = client.get(f"/surveys/{s['id']}/costing")
    assert cost.status_code == 200 and "NVR 8 canales 4K PoE" in cost.json()["missing_cost"]  # sin costo, como siempre
    # un error dentro del motor (no del catalogo): la respuesta es la sugerencia de siempre, sin candidatos
    monkeypatch.undo()
    monkeypatch.setattr(equipment_reco, "features", caido)
    plain = client.post(f"/surveys/{s['id']}/suggest?candidates=true")
    assert plain.status_code == 200 and all("candidates" not in x for x in plain.json())
