"""Tienda, reunion del 21/10: fotos fijas de los equipos de cada kit (y elegidas a mano en Mi Tienda), revision del
correo del comprador en el checkout (formato, errores de dedo, dominio con MX sin bloquear si el DNS no responde),
tarifa GAM por defecto de 4 000 y aviso interno por cada envio a domicilio para coordinar el mensajero."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import EmailOutbox, Order, Product
from tests.test_store_vitrina import _catalogo, _publicar
from tests.test_tienda_checkout import SLUG, _body, inv_calls  # noqa: F401 (fixture)


@pytest.fixture(autouse=True)
def _limites():
    from app.routers.store_showcase import reset_limiters

    reset_limiters()
    yield
    reset_limiters()


def _tienda(client, **ck):
    client.put("/store", json={"kind": "tienda", "published": True, "whatsapp": "+50688889892"})
    cfg = {
        "methods": {
            "sinpe": {"active": True, "phone": "8888-7777", "holder": "Crimson SA"},
            "transferencia": {"active": False},
            "tarjeta": {"active": False},
            "efectivo": {"active": True, "pickup_only": ck.pop("pickup_only", True)},
        },
        "auto_invoice": False,
        "pickup": {"address": "Oficinas Crimson, Escazú", "hours": "L-V 8-17"},
        **ck,
    }
    r = client.put("/store/checkout", json=cfg)
    assert r.status_code == 200, r.text
    return client.get(f"{SLUG}/products").json()


def _avisos_envio(db, oid: int) -> list[EmailOutbox]:
    rows = db.scalars(select(EmailOutbox).where(EmailOutbox.entity == "order", EmailOutbox.entity_id == oid)).all()
    return [m for m in rows if m.subject.startswith("Envío a domicilio por coordinar")]


# ---------------------------------------------------------------- item 28: fotos fijas de los kits
def test_kits_con_fotos_de_sus_equipos_y_elegidas_a_mano(client, auth, db_session):
    _publicar(client)
    prods = _catalogo(db_session)
    h = client.get("/public/store/crimson/showcase").json()
    k = next(x for x in h["kits"] if x["id"] == "negocio-8")
    fotos = k["photos"]
    # los equipos reales del kit, uno por producto: camaras, grabador, disco (no la misma camara repetida)
    assert 2 <= len(fotos) <= 4 and len({f["image"] for f in fotos}) == len(fotos)
    etiquetas = [f["label"] for f in fotos]
    assert etiquetas[0] == "Cámara exterior" and "Grabador" in etiquetas
    assert k["image"] == fotos[0]["image"]
    assert all(f["image"].startswith("https://cdn.example.com/") for f in fotos)

    # Mi Tienda -> Vitrina: Andres elige que equipos (y cual foto) muestra el kit; lo ajeno o sin foto se descarta
    cam = prods["DS-2CD1043G2-LIU"]
    cam.images = [{"url": "https://cdn.example.com/bullet.jpg", "main": True}, {"url": "https://cdn.example.com/bullet-lado.jpg"}]
    db_session.commit()
    kits = client.get("/store/showcase").json()["kits"]
    kits[0]["photos"] = [
        {"product_id": cam.id, "image": "https://cdn.example.com/bullet-lado.jpg"},
        {"product_id": prods["WD44PURZ"].id, "image": "https://otro.example.com/no-es-suya.jpg"},
        {"product_id": prods["RJ45-100"].id},  # sin foto
        {"product_id": 999999},
    ]
    r = client.put("/store/showcase", json={"kits": kits})
    assert r.status_code == 200, r.text
    guardado = r.json()["kits"][0]["photos"]
    assert guardado == [{"product_id": cam.id, "image": "https://cdn.example.com/bullet-lado.jpg"}, {"product_id": prods["WD44PURZ"].id, "image": ""}]
    pv = next(x for x in r.json()["kits_preview"] if x["id"] == kits[0]["id"])
    assert pv["photos_manual"] and pv["photos"][0]["images"] == ["https://cdn.example.com/bullet.jpg", "https://cdn.example.com/bullet-lado.jpg"]
    assert client.put("/store/showcase", json={"kits": [{**kits[0], "photos": [{"product_id": cam.id}] * 5}]}).status_code == 422

    h = client.get("/public/store/crimson/showcase").json()
    k0 = next(x for x in h["kits"] if x["id"] == kits[0]["id"])
    assert [f["image"] for f in k0["photos"]] == ["https://cdn.example.com/bullet-lado.jpg", "https://cdn.example.com/wd4.jpg"]
    assert k0["photos"][1]["label"] == "Disco duro"

    # "Arma tu sistema" abierto con las respuestas de ese kit muestra las mismas fotos; otras respuestas, las del kit
    r = client.post("/public/store/crimson/kit", json=kits[0]["answers"]).json()
    assert [f["image"] for f in r["photos"]] == [f["image"] for f in k0["photos"]]
    otro = client.post("/public/store/crimson/kit", json={"place": "condominio", "indoor": 1, "outdoor": 1, "days": 7, "remote": False}).json()
    assert otro["photos"] and otro["photos"][0]["label"] == "Cámara exterior"

    # un producto oculto de la web no se puede elegir (se descarta al guardar)...
    oculto = db_session.scalar(select(Product).where(Product.code == "OCULTO"))
    oculto.images = [{"url": "https://cdn.example.com/oculta.jpg", "main": True}]
    db_session.commit()
    r = client.put("/store/showcase", json={"kits": [{**kits[0], "photos": [{"product_id": oculto.id}, *guardado]}, *kits[1:]]})
    assert r.status_code == 200, r.text
    assert r.json()["kits"][0]["photos"] == guardado and all(f["product_id"] != oculto.id for f in r.json()["kits"][0]["photos"])
    # ...y uno elegido que se oculta despues no sale en la vitrina publica ni en "Arma tu sistema"
    cam.show_on_web = False
    db_session.commit()
    k0 = next(x for x in client.get("/public/store/crimson/showcase").json()["kits"] if x["id"] == kits[0]["id"])
    assert [f["image"] for f in k0["photos"]] == ["https://cdn.example.com/wd4.jpg"]
    r = client.post("/public/store/crimson/kit", json=kits[0]["answers"]).json()
    assert r["photos"] and all(f["image"] != "https://cdn.example.com/bullet-lado.jpg" for f in r["photos"])
    cam.show_on_web = True
    db_session.commit()

    # un producto elegido que se desactiva: la tarjeta vuelve a las fotos de sus equipos
    cam.active = False
    prods["WD44PURZ"].active = False
    db_session.commit()
    k0 = next(x for x in client.get("/public/store/crimson/showcase").json()["kits"] if x["id"] == kits[0]["id"])
    assert k0["photos"] and all(f["image"] not in ("https://cdn.example.com/bullet-lado.jpg", "https://cdn.example.com/wd4.jpg") for f in k0["photos"])


# ---------------------------------------------------------------- item 32: correo real en el checkout
@pytest.fixture
def dns(monkeypatch):
    from app.services import email_check

    respuestas = {"noexiste.cr": "nodomain", "sinmx.com": "nomx", "gmail.com": "ok", "correo.com": "ok", "caido.com": "unknown"}
    consultas: list[str] = []

    def fake(domain):
        consultas.append(domain)
        return respuestas.get(domain, "ok")

    monkeypatch.setattr(email_check, "_query_mx", fake)
    return consultas


def test_revisar_correo_formato_dedo_y_dominio(client, auth, dns):
    _tienda(client)
    chk = lambda e: client.post(f"{SLUG}/email-check", json={"email": e}).json()  # noqa: E731
    assert chk("ana@") == {"ok": False, "reason": "formato", "message": "Escribí un correo válido: ahí te llega la confirmación", "suggestion": None}
    r = chk("Ana.Vega@GMIAL.com")
    assert not r["ok"] and r["reason"] == "error_de_dedo" and r["suggestion"] == "Ana.Vega@gmail.com" and "gmail.com" in r["message"]
    assert chk("luis@hotmial.com")["suggestion"] == "luis@hotmail.com"
    assert "gmial.com" not in dns  # el error de dedo se corrige sin consultar el DNS
    r = chk("x@noexiste.cr")
    assert not r["ok"] and r["reason"] == "dominio" and "no existe" in r["message"]
    r = chk("x@sinmx.com")
    assert not r["ok"] and r["reason"] == "sin_correo"
    assert chk("ana@gmail.com")["ok"] is True
    assert chk("ana@caido.com") == {"ok": True, "reason": None, "message": None, "suggestion": None}  # DNS sin respuesta: no se bloquea
    # el mismo dominio no se consulta dos veces (cache); "sin respuesta" tambien se guarda, pero solo un minuto
    chk("otra@gmail.com")
    chk("otra@caido.com")
    assert dns.count("gmail.com") == 1 and dns.count("caido.com") == 1
    # nombres de redes privadas (los del hosting): no se consultan ni se distingue "no existe" de "no recibe"
    for e in ("x@postgres.railway.internal", "x@nas.lan", "x@srv.corp", "x@router.home.arpa"):
        assert chk(e)["reason"] == "formato", e
    assert not any(d.endswith((".internal", ".lan", ".corp", ".arpa")) for d in dns)


def test_checkout_rechaza_correo_sin_dominio_y_respeta_el_ajuste(client, auth, dns, inv_calls):  # noqa: F811
    prods = _tienda(client)
    pid = prods[0]["id"]
    contacto = lambda e: {"name": "Ana Vega", "email": e, "phone": "8888-0000"}  # noqa: E731
    r = client.post(f"{SLUG}/checkout", json=_body(pid, contact=contacto("ana@noexiste.cr"), idempotency_key="ck-mail-0001"))
    assert r.status_code == 422 and "no existe" in r.json()["detail"]
    r = client.post(f"{SLUG}/checkout", json=_body(pid, contact=contacto("ana@gmial.com"), idempotency_key="ck-mail-0002"))
    assert r.status_code == 422 and "ana@gmail.com" in r.json()["detail"]
    assert client.post(f"{SLUG}/checkout", json=_body(pid, contact=contacto("ana@caido.com"), idempotency_key="ck-mail-0003")).status_code == 201
    assert client.get(f"{SLUG}/checkout-config").json()["verify_email"] is True

    # Mi Tienda puede apagar la revision por DNS (el formato sigue validandose)
    _tienda(client, verify_email=False)
    assert client.get(f"{SLUG}/checkout-config").json()["verify_email"] is False
    assert client.post(f"{SLUG}/email-check", json={"email": "x@noexiste.cr"}).json()["ok"] is True
    assert client.post(f"{SLUG}/checkout", json=_body(pid, contact=contacto("ana@noexiste.cr"), idempotency_key="ck-mail-0004")).status_code == 201
    # ...pero los errores de dedo se corrigen siempre, tambien en el servidor (un cliente viejo o una llamada directa)
    r = client.post(f"{SLUG}/checkout", json=_body(pid, contact=contacto("ana@gmial.com"), idempotency_key="ck-mail-0005"))
    assert r.status_code == 422 and "ana@gmail.com" in r.json()["detail"]
    assert client.post(f"{SLUG}/email-check", json={"email": "ana@gmial.com"}).json()["reason"] == "error_de_dedo"


def test_revisar_correo_tiene_tope_por_ip(client, auth):
    _tienda(client)
    codes = [client.post(f"{SLUG}/email-check", json={"email": f"a{i}@correo.com"}).status_code for i in range(42)]
    assert codes[0] == 200 and codes[-1] == 429


def test_revisar_correo_tiene_tope_global(client, auth, from_ip, monkeypatch):
    from app.core.ratelimit import email_check_global_limiter

    _tienda(client)
    monkeypatch.setattr(email_check_global_limiter, "limit", 3)  # muchas IPs distintas: frena el tope global
    codes = [client.post(f"{SLUG}/email-check", json={"email": f"a{i}@correo.com"}, headers=from_ip(f"190.10.0.{i + 1}")).status_code for i in range(4)]
    assert codes == [200, 200, 200, 429]


# ---------------------------------------------------------------- item 33: envio GAM 4 000 y aviso del mensajero
def test_tarifa_gam_por_defecto_y_la_guardada_manda(client, auth):
    prods = _tienda(client)
    q = client.post(f"{SLUG}/quote", json=_body(prods[0]["id"])).json()
    assert Decimal(str(q["shipping"])) == Decimal(4000)
    gam = next(r for r in client.get(f"{SLUG}/checkout-config").json()["shipping_rates"] if r["name"] == "Envío GAM")
    assert Decimal(str(gam["display_amount"])) == Decimal(4520)  # con IVA
    # la tarifa que la tienda guardo en Mi Tienda no se toca
    client.put("/store", json={"shipping_rates": [{"name": "Envío GAM", "amount": 3500, "active": True, "zone": "gam"}]})
    assert Decimal(str(client.post(f"{SLUG}/quote", json=_body(prods[0]["id"])).json()["shipping"])) == Decimal(3500)


def test_aviso_interno_por_cada_envio_a_domicilio(client, auth, db_session, inv_calls):  # noqa: F811
    prods = _tienda(client)
    pid = prods[0]["id"]
    o = client.post(f"{SLUG}/checkout", json=_body(pid, idempotency_key="ck-envio-0001"))
    assert o.status_code == 201, o.text
    oid = db_session.scalar(select(Order.id).where(Order.number == o.json()["number"]))
    assert _avisos_envio(db_session, oid) == []  # sin pagar todavia: no se coordina mensajero
    st = lambda s, **kw: client.post(f"/store-orders/{oid}/status", json={"status": s, **kw})  # noqa: E731
    assert st("pagado", payment_ref="SINPE 1").status_code == 200
    avisos = _avisos_envio(db_session, oid)
    assert len(avisos) == 1 and avisos[0].to == "admin@crimsonapp.com"
    assert "Escazú" in avisos[0].subject and "200 m norte del parque" in avisos[0].html and "8888-0000" in avisos[0].html
    assert "Pagado" in avisos[0].html
    assert st("preparando").status_code == 200 and st("enviado", tracking_number="X1").status_code == 200
    assert len(_avisos_envio(db_session, oid)) == 1  # uno solo por pedido

    # el endpoint generico de ordenes (eventos, POS, integraciones) tambien avisa, una sola vez
    o = client.post(f"{SLUG}/checkout", json=_body(pid, idempotency_key="ck-envio-0005"))
    gid = db_session.scalar(select(Order.id).where(Order.number == o.json()["number"]))
    assert client.patch(f"/orders/{gid}", json={"status": "pagado"}).status_code == 200
    assert client.patch(f"/orders/{gid}", json={"status": "pagado"}).status_code == 200
    assert len(_avisos_envio(db_session, gid)) == 1

    # retiro en sitio: no hay mensajero que coordinar
    r = client.post(f"{SLUG}/checkout", json=_body(pid, delivery={"type": "retiro"}, shipping_method="Retiro en tienda", idempotency_key="ck-envio-0002"))
    rid = db_session.scalar(select(Order.id).where(Order.number == r.json()["number"]))
    client.post(f"/store-orders/{rid}/status", json={"status": "pagado"})
    assert _avisos_envio(db_session, rid) == []


def test_aviso_de_envio_en_efectivo_contra_entrega_y_apagado(client, auth, db_session, inv_calls):  # noqa: F811
    prods = _tienda(client, pickup_only=False)
    pid = prods[0]["id"]
    o = client.post(f"{SLUG}/checkout", json=_body(pid, payment_method="efectivo", idempotency_key="ck-envio-0003"))
    assert o.status_code == 201, o.text
    oid = db_session.scalar(select(Order.id).where(Order.number == o.json()["number"]))
    avisos = _avisos_envio(db_session, oid)
    assert len(avisos) == 1 and "cobrar al entregar" in avisos[0].html

    _tienda(client, pickup_only=False, delivery_notice=False)
    o = client.post(f"{SLUG}/checkout", json=_body(pid, idempotency_key="ck-envio-0004"))
    oid = db_session.scalar(select(Order.id).where(Order.number == o.json()["number"]))
    client.post(f"/store-orders/{oid}/status", json={"status": "pagado"})
    assert _avisos_envio(db_session, oid) == []
