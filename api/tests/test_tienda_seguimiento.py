"""Reunion 21/10 · pagina de seguimiento del pedido (items 34, 36, 38 y 39).

- 39: el enlace de seguimiento daba "No encontramos ese pedido". Se reproduce con el enlace tal como llega en la
  vida real: con una letra de mas al final (asi estaba en la grabacion), con el punto de la oracion de WhatsApp,
  partido por el cliente de correo, %-codificado o firmado por un contenedor con el reloj un poco adelantado.
  Lo alterado de verdad (firma, otro pedido, otra tienda, otro proposito) sigue dando 404.
- 36: historial con las notas que la empresa pone PARA EL CLIENTE; las internas nunca salen.
- 34: el cliente vuelve a ver el comprobante que subio, solo con el enlace de su pedido.
- 38: la version impresa lleva logo, datos, lineas, totales y estado.
"""

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import jwt

from app.core.config import settings
from tests.test_tienda_checkout import PNG, SLUG, _body, _shop


def _pedido(client, key="ck-seg-0001", **kw):
    prods = _shop(client)
    r = client.post(f"{SLUG}/checkout", json=_body(prods[0]["id"], idempotency_key=key, **kw))
    assert r.status_code == 201, r.text
    o = r.json()
    oid = next(x["id"] for x in client.get("/store-orders").json()["items"] if x["number"] == o["number"])
    return o, oid


def _ver(client, tok):
    return client.get(f"{SLUG}/orders/{quote(tok, safe='')}")


# ---------- 39: el enlace de seguimiento ----------
def test_enlace_con_basura_pegada_igual_abre_el_pedido(client, auth):
    o, _ = _pedido(client)
    tok = o["track_token"]
    assert o["track_url"].endswith(f"?pedido={tok}")
    assert _ver(client, tok).json()["number"] == o["number"]
    # lo que se vio en la grabacion: el mismo enlace con una "v" de mas al final -> antes 404
    for sucio in (tok + "v", tok + "vv", tok + ".", tok + ")", tok + ",", f" {tok}\n", tok[:40] + "\n" + tok[40:], f"<{tok}>", quote(tok)):
        r = _ver(client, sucio)
        assert r.status_code == 200, (sucio, r.text)
        assert r.json()["number"] == o["number"]


def test_enlace_firmado_con_reloj_adelantado_abre(client, auth):
    """PyJWT rechaza un iat en el futuro: el enlace que firma el worker (o la otra replica) con el reloj unos
    segundos adelante fallaba al abrirse enseguida."""
    o, oid = _pedido(client)
    pub = _ver(client, o["track_token"]).json()
    payload = jwt.decode(o["track_token"], settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    now = datetime.now(UTC)
    adelantado = jwt.encode(
        {**payload, "iat": now + timedelta(seconds=90), "exp": now + timedelta(days=400)}, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    r = _ver(client, adelantado)
    assert r.status_code == 200 and r.json()["number"] == pub["number"]
    # demasiado en el futuro no: no es reloj, es un token raro
    raro = jwt.encode({**payload, "iat": now + timedelta(hours=2), "exp": now + timedelta(days=400)}, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    assert _ver(client, raro).status_code == 404
    # vencido hace rato: 404
    viejo = jwt.encode({**payload, "iat": now - timedelta(days=500), "exp": now - timedelta(days=1)}, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    assert _ver(client, viejo).status_code == 404


def test_lo_alterado_sigue_sin_abrir(client, auth):
    o, _ = _pedido(client)
    tok = o["track_token"]
    head, body, sig = tok.split(".")
    otro = "A" if sig[0] != "A" else "B"
    assert _ver(client, f"{head}.{body}.{otro}{sig[1:]}").status_code == 404  # firma cambiada
    assert _ver(client, f"{head}.{body}.{sig[:-5]}").status_code == 404  # firma cortada
    assert _ver(client, "no-es-un-token").status_code == 404
    assert _ver(client, f"{tok[:-3]}xyz").status_code == 404
    # el mismo cuerpo con otro pedido no valida (la firma cubre o y t)
    payload = jwt.decode(tok, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    falso = jwt.encode({**payload, "o": payload["o"] + 1}, "otra-llave-de-al-menos-32-caracteres-xx", algorithm="HS256")
    assert _ver(client, falso).status_code == 404
    # con la basura pegada tampoco se abre otro pedido ni otra tienda
    assert client.get(f"/public/store/otra-tienda/orders/{tok}v").status_code == 404


# ---------- 36: historial con notas para el cliente ----------
def test_historial_muestra_notas_para_el_cliente_y_esconde_las_internas(client, auth):
    o, oid = _pedido(client, delivery={"type": "retiro"}, shipping_method="Retiro en tienda")
    tok = o["track_token"]
    st = lambda s, **kw: client.post(f"/store-orders/{oid}/status", json={"status": s, **kw})  # noqa: E731
    assert st("pagado", note="Pago visto en el banco", notify=False).status_code == 200
    assert st("preparando", note="Pedido realizado al proveedor", notify=False).status_code == 200
    r = st("listo", client_note="Presentar comprobante con el número de pedido", note="Lo dejó la bodega", notify=False)
    assert r.status_code == 200
    det = r.json()
    h = next(x for x in det["history"] if x["status"] == "listo")
    assert h["note"] == "Lo dejó la bodega" and h["client_note"] == "Presentar comprobante con el número de pedido"
    # nota suelta (sin cambiar estado): una para el cliente y una interna
    assert client.post(f"/store-orders/{oid}/note", json={"note": "Horario extendido hoy hasta las 7 p. m.", "client": True}).status_code == 200
    assert client.post(f"/store-orders/{oid}/note", json={"note": "Cliente frecuente, ofrecer garantía extendida"}).status_code == 200
    assert client.post(f"/store-orders/{oid}/note", json={"note": "  "}).status_code == 422

    pub = client.get(f"{SLUG}/orders/{tok}").json()
    texto = json.dumps(pub, ensure_ascii=False)
    for interna in ("Pago visto en el banco", "Pedido realizado al proveedor", "Lo dejó la bodega", "garantía extendida"):
        assert interna not in texto
    listo = next(x for x in pub["history"] if x["status"] == "listo" and x.get("kind") != "nota")
    assert listo["note"] == "Presentar comprobante con el número de pedido" and listo["label"] == "Listo para retiro"
    suelta = [x for x in pub["history"] if x.get("kind") == "nota"]
    assert [x["note"] for x in suelta] == ["Horario extendido hoy hasta las 7 p. m."]
    # los pasos siguen en orden y sin notas del sistema
    assert [x["status"] for x in pub["history"] if x.get("kind") != "nota"] == ["nuevo", "pagado", "preparando", "listo"]
    assert all(x["note"] is None for x in pub["history"] if x["status"] in ("nuevo", "pagado", "preparando"))
    # el panel ve ambas
    hist = client.get(f"/store-orders/{oid}").json()["history"]
    assert any(x.get("kind") == "nota" and x.get("client_note") for x in hist) and any(
        x.get("note") == "Cliente frecuente, ofrecer garantía extendida" for x in hist
    )


def test_motivo_de_rechazo_y_cancelacion_los_ve_el_cliente(client, auth):
    o, oid = _pedido(client, key="ck-seg-0002")
    tok = o["track_token"]
    assert client.post(f"{SLUG}/orders/{tok}/proof", files={"file": ("sinpe.png", PNG, "image/png")}).status_code == 201
    r = client.post(f"/store-orders/{oid}/status", json={"status": "nuevo", "note": "El monto no coincide", "notify": False})
    assert r.status_code == 200
    c = client.post(f"/store-orders/{oid}/status", json={"status": "cancelado", "note": "Cliente desistió", "notify": False})
    assert c.status_code == 200
    pub = client.get(f"{SLUG}/orders/{tok}").json()
    notas = {x["status"]: x["note"] for x in pub["history"]}
    assert notas["nuevo"] in (None, "El monto no coincide")
    assert [x["note"] for x in pub["history"] if x["status"] == "nuevo"][-1] == "El monto no coincide"
    assert notas["cancelado"] == "Cliente desistió"


def test_transition_no_publica_la_nota_interna(client, auth, db_session):
    """La frontera interna/publica la marca `client_note`, no el estado: un llamador del servicio (worker, webhook,
    integracion) que rechaza un comprobante con una nota operativa no la publica."""
    from app.models import Order, Tenant
    from app.services import store_orders as so

    o, oid = _pedido(client, key="ck-seg-0004")
    tok = o["track_token"]
    assert client.post(f"{SLUG}/orders/{tok}/proof", files={"file": ("sinpe.png", PNG + b"x", "image/png")}).status_code == 201
    ped = db_session.get(Order, oid)
    t = db_session.get(Tenant, ped.tenant_id)
    so.transition(db_session, t, ped, "nuevo", by="sistema", note="Revision automatica: cargo ch_interno_123", notify=False)
    db_session.commit()
    pub = client.get(f"{SLUG}/orders/{tok}").json()
    assert "ch_interno_123" not in json.dumps(pub, ensure_ascii=False)
    # el vencimiento del sistema sigue diciendo por que se cancelo (cancel_reason, que ya era publico)
    so.transition(db_session, t, ped, "cancelado", by="sistema", note="Venció sin pago", notify=False)
    db_session.commit()
    pub = client.get(f"{SLUG}/orders/{tok}").json()
    assert [x["note"] for x in pub["history"] if x["status"] == "cancelado"] == ["Venció sin pago"]
    assert "client_note" not in next(h for h in db_session.get(Order, oid).history if h.get("status") == "cancelado")


# ---------- 34: el cliente ve su comprobante ----------
def test_cliente_ve_su_comprobante_solo_con_su_enlace(client, auth):
    a, _ = _pedido(client, key="ck-seg-000A")
    b, _ = _pedido(client, key="ck-seg-000B", contact={"name": "Luis Mora", "email": "luis@correo.com", "phone": "88887777"})
    ta, tb = a["track_token"], b["track_token"]
    assert client.post(f"{SLUG}/orders/{ta}/proof", files={"file": ("sinpe-a.png", PNG + b"a", "image/png")}).status_code == 201
    assert client.post(f"{SLUG}/orders/{tb}/proof", files={"file": ("pago-b.pdf", b"%PDF-1.4 comprobante b", "application/pdf")}).status_code == 201

    pa = client.get(f"{SLUG}/orders/{ta}").json()
    assert pa["proofs"][0]["n"] == 0 and pa["proofs"][0]["filename"] == "sinpe-a.png" and pa["proofs"][0]["pdf"] is False
    assert "media_id" not in json.dumps(pa)  # sin ids internos
    f = client.get(f"{SLUG}/orders/{ta}/proofs/0", headers={"Authorization": ""})
    assert f.status_code == 200 and f.content == PNG + b"a" and f.headers["content-type"] == "image/png"
    assert "no-store" in f.headers["cache-control"] and "sandbox" in f.headers["content-security-policy"]
    assert f.headers["x-content-type-options"] == "nosniff"
    fb = client.get(f"{SLUG}/orders/{tb}/proofs/0", headers={"Authorization": ""})
    assert fb.status_code == 200 and fb.content.startswith(b"%PDF") and fb.headers["content-type"] == "application/pdf"
    # el PDF se abre en una pestana nueva (enlace directo): con CSP `sandbox` el visor de Chrome lo deja en blanco
    assert "sandbox" not in fb.headers.get("content-security-policy", "")
    assert fb.headers["x-content-type-options"] == "nosniff" and "no-store" in fb.headers["cache-control"]
    assert fb.headers["content-disposition"] == 'inline; filename="pago-b.pdf"'
    # el enlace de A no alcanza el comprobante de B (solo hay uno en A) ni posiciones raras
    for n in (1, 5, -1):
        assert client.get(f"{SLUG}/orders/{ta}/proofs/{n}", headers={"Authorization": ""}).status_code == 404
    # sin token valido, nada
    assert client.get(f"{SLUG}/orders/{ta[:-3]}xyz/proofs/0", headers={"Authorization": ""}).status_code == 404
    # sigue visible despues de confirmado el pago (la seccion de pagar ya no sale, el comprobante si)
    oid = next(x["id"] for x in client.get("/store-orders").json()["items"] if x["number"] == a["number"])
    assert client.post(f"/store-orders/{oid}/status", json={"status": "pagado", "notify": False}).status_code == 200
    pa = client.get(f"{SLUG}/orders/{ta}").json()
    assert pa["instructions"] is None and len(pa["proofs"]) == 1 and pa["proofs"][0]["open"] is True
    assert client.get(f"{SLUG}/orders/{ta}/proofs/0", headers={"Authorization": ""}).status_code == 200


def test_comprobante_no_queda_colgado_del_enlace(client, auth, db_session):
    """El enlace dura 400 dias y se reenvia: el comprobante (cuenta, telefono SINPE, titular) solo se sirve mientras
    el pedido siga abierto y por so.PROOF_VIEW_DAYS desde que se subio."""
    from app.models import Order
    from app.services import store_orders as so

    a, oid = _pedido(client, key="ck-seg-000C")
    ta = a["track_token"]
    assert client.post(f"{SLUG}/orders/{ta}/proof", files={"file": ("sinpe-c.png", PNG + b"c", "image/png")}).status_code == 201
    assert client.get(f"{SLUG}/orders/{ta}/proofs/0", headers={"Authorization": ""}).status_code == 200
    # subido hace mas de 30 dias: el nombre sigue en la pagina, el archivo no
    o = db_session.get(Order, oid)
    viejo = (datetime.now(UTC) - timedelta(days=so.PROOF_VIEW_DAYS + 1)).isoformat()
    o.proofs = [{**o.proofs[0], "at": viejo}]
    db_session.commit()
    pa = client.get(f"{SLUG}/orders/{ta}").json()
    assert pa["proofs"][0]["open"] is False and pa["proofs"][0]["filename"] == "sinpe-c.png"
    assert client.get(f"{SLUG}/orders/{ta}/proofs/0", headers={"Authorization": ""}).status_code == 404
    # reciente pero el pedido ya se cerro (cancelado): tampoco
    o.proofs = [{**o.proofs[0], "at": datetime.now(UTC).isoformat()}]
    db_session.commit()
    assert client.post(f"/store-orders/{oid}/status", json={"status": "cancelado", "note": "Cliente desistió", "notify": False}).status_code == 200
    assert client.get(f"{SLUG}/orders/{ta}").json()["proofs"][0]["open"] is False
    assert client.get(f"{SLUG}/orders/{ta}/proofs/0", headers={"Authorization": ""}).status_code == 404


def test_comprobante_repetido_muestra_el_nombre_que_subio_cada_cliente(client, auth):
    """La multimedia se deduplica por sha256 en toda la empresa: el nombre del pedido B no puede ser el que puso A."""
    a, _ = _pedido(client, key="ck-seg-000D")
    b, _ = _pedido(client, key="ck-seg-000E", contact={"name": "Cliente Dos", "email": "dos@correo.com", "phone": "88886666"})
    mismo = PNG + b"mismo-archivo"
    assert client.post(f"{SLUG}/orders/{a['track_token']}/proof", files={"file": ("otro-cliente.png", mismo, "image/png")}).status_code == 201
    assert client.post(f"{SLUG}/orders/{b['track_token']}/proof", files={"file": ("mi-pago.png", mismo, "image/png")}).status_code == 201
    pb = client.get(f"{SLUG}/orders/{b['track_token']}").json()
    assert pb["proofs"][0]["filename"] == "mi-pago.png"
    fb = client.get(f"{SLUG}/orders/{b['track_token']}/proofs/0", headers={"Authorization": ""})
    assert fb.status_code == 200 and fb.headers["content-disposition"] == 'inline; filename="mi-pago.png"'


# ---------- 38: version impresa ----------
def test_version_impresa_presentable(client, auth, db_session):
    from app.models import Media, Tenant

    logo = client.post("/media", files={"file": ("logo.png", PNG + b"logo", "image/png")})
    assert logo.status_code in (200, 201), logo.text
    key = logo.json()["key"] if "key" in logo.json() else db_session.get(Media, logo.json()["id"]).key
    o, oid = _pedido(client, key="ck-seg-0003", contact={"name": "Ana <b>Vega</b>", "email": "ana@correo.com", "phone": "88880000"})
    t = db_session.query(Tenant).filter(Tenant.slug == "crimson").one()
    t.logo_url = f"/api/media/f/{key}"
    t.legal_name, t.tax_id = "Crimson Consulting SA", "3-101-000000"
    db_session.commit()
    assert client.post(f"/store-orders/{oid}/status", json={"status": "pagado", "payment_ref": "SINPE 998877", "notify": False}).status_code == 200
    pr = client.get(f"/store-orders/{oid}/print")
    assert pr.status_code == 200 and pr.headers["content-type"].startswith("text/html")
    h = pr.text
    assert "data:image/png;base64," in h  # logo incrustado (dentro del blob una ruta relativa no carga)
    for txt in (
        o["number"],
        "Crimson Consulting SA",
        "3-101-000000",
        "Ana &lt;b&gt;Vega&lt;/b&gt;",
        "Precio s/IVA",
        "Subtotal (sin IVA)",
        "IVA",
        "Total",
        "Pagado",
        "SINPE 998877",
        "Firma",
    ):
        assert txt in h, txt
    assert "img-src data: https:" in pr.headers["content-security-policy"]
    # sin logo igual sale (con el nombre)
    t.logo_url = None
    db_session.commit()
    h2 = client.get(f"/store-orders/{oid}/print").text
    assert "data:image" not in h2 and o["number"] in h2
    # los datos del cliente van escapados
    assert "<b>Vega</b>" not in h2


def test_version_impresa_no_repite_el_metodo_de_entrega(client, auth):
    o, oid = _pedido(client, key="ck-seg-0005", delivery={"type": "retiro"}, shipping_method="Retiro en tienda")
    h = client.get(f"/store-orders/{oid}/print").text
    caja = h.split("<div class=k>Entrega</div>", 1)[1].split("</div>", 1)[0]
    assert caja.count("Retiro en sitio") == 1, caja
    o2, oid2 = _pedido(client, key="ck-seg-0006")
    h2 = client.get(f"/store-orders/{oid2}/print").text
    caja2 = h2.split("<div class=k>Entrega</div>", 1)[1].split("</div>", 1)[0]
    metodo = caja2.split("<b>", 1)[1].split("</b>", 1)[0]
    assert metodo and caja2.count(metodo) == 1, caja2
