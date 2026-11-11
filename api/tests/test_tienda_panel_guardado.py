"""O4-02: un solo modelo de guardado en Mi Tienda.

- Version optimista en PUT /store, /store/showcase y /store/checkout: quien guarda con una version vieja recibe 409
  con el mensaje para el panel; sin version (pestanas viejas) se guarda como siempre.
- Historial (GET /store/history): ultimos cambios con quien, cuando y que; sin numeros de cuenta ni textos legales.
- Restaurar (POST /store/history/{id}/restore): solo campos de la lista blanca; nunca publicacion, dominio, tipo de
  tienda ni moneda; marca el catalogo para que la tienda lo muestre.
- Editar titular y banner en la misma sesion no pierde nada.
Datos ficticios."""

from sqlalchemy import select

from app.models import AuditLog, Tenant
from tests.test_session4 import _user_with_role

IBAN = "CR05015201001026284066"
SINPE = "8888-7777"
CONFLICTO = "Alguien más guardó cambios en esta sección. Recargue para verlos."


def _v(client, ruta):
    r = client.get(ruta)
    assert r.status_code == 200, r.text
    return r.json()["version"]


def test_guardado_concurrente_responde_409_en_las_tres_secciones(client, auth):
    for ruta, cuerpo in (
        ("/store", {"tagline": "Frase A"}),
        ("/store/showcase", {"headline": "Titular A"}),
        ("/store/checkout", {"methods": {"sinpe": {"active": True, "phone": SINPE, "holder": "Comercio de Prueba"}}}),
    ):
        v = _v(client, ruta)
        r1 = client.put(ruta, json={**cuerpo, "version": v})  # la primera pestana guarda
        assert r1.status_code == 200, r1.text
        assert r1.json()["version"] == v + 1
        r2 = client.put(ruta, json={**cuerpo, "version": v})  # la segunda leyo antes: no pisa
        assert r2.status_code == 409, r2.text
        assert r2.json()["detail"] == CONFLICTO
        assert client.put(ruta, json=cuerpo).status_code == 200  # sin version: como siempre (contrato aditivo)


def test_la_version_de_una_seccion_no_bloquea_a_otra(client, auth):
    vs, vk = _v(client, "/store"), _v(client, "/store/showcase")
    assert client.put("/store", json={"tagline": "Otra frase", "version": vs}).status_code == 200
    assert client.put("/store/showcase", json={"headline": "Otro titular", "version": vk}).status_code == 200


def test_editar_titular_y_banner_en_la_misma_sesion_no_pierde_nada(client, auth):
    v = _v(client, "/store/showcase")
    banner = {"image": "/media/prueba.png", "title": "Promo de prueba", "text": "", "link": "/catalogo", "cta": "Ver", "position": 1, "active": True}
    r = client.put("/store/showcase", json={"headline": "Titular nuevo", "banners": [banner], "version": v})
    assert r.status_code == 200, r.text
    sc = client.get("/store/showcase").json()
    assert sc["headline"] == "Titular nuevo" and [b["title"] for b in sc["banners"]] == ["Promo de prueba"]


def test_historial_sin_cuentas_ni_textos_legales(client, auth, db_session):
    tid = auth["tenant"]["id"]
    assert client.put("/store", json={"tagline": "Frase vieja", "name": "Tienda vieja"}).status_code == 200
    assert client.put("/store", json={"tagline": "Frase nueva", "legal": {"terms": "Términos de prueba con el SINPE 8888-7777"}}).status_code == 200
    ck = {"methods": {"sinpe": {"active": True, "phone": SINPE, "holder": "Comercio"}, "transferencia": {"active": True, "iban": IBAN, "holder": "Comercio"}}}
    assert client.put("/store/checkout", json=ck).status_code == 200
    assert client.put("/store/showcase", json={"pages": {"envios": "Política de envíos de prueba"}}).status_code == 200
    h = client.get("/store/history")
    assert h.status_code == 200, h.text
    rows = h.json()
    assert len(rows) >= 4 and len(rows) <= 20
    top = rows[0]
    assert {"id", "at", "quien", "seccion", "campos", "restaurables"} <= set(top)
    assert top["quien"]  # quien guardo
    # nada de cuentas ni de textos legales completos en lo guardado
    logs = db_session.scalars(select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.entity == "store_history")).all()
    texto = " ".join(str(x.before) + str(x.after) + str(x.diff) for x in logs) + str(rows)
    for prohibido in ("015201001026284066", "8888-7777", "88887777", "Términos de prueba", "Política de envíos de prueba"):
        assert prohibido not in texto, prohibido
    assert "huella" in texto  # de los textos legales queda su huella


def test_restaurar_no_cambia_publicacion_dominio_ni_moneda(client, auth, db_session):
    tid = auth["tenant"]["id"]
    assert client.put("/store", json={"tagline": "Frase original", "published": True, "currency": "CRC", "domain": ""}).status_code == 200
    assert client.put("/store", json={"tagline": "Frase cambiada", "published": False, "currency": "USD", "domain": "tienda.ejemplo.com"}).status_code == 200
    v0 = (db_session.get(Tenant, tid).settings or {}).get("catalog_v") or 0
    fila = next(x for x in client.get("/store/history").json() if x["seccion"] == "store" and "Frase" in x["restaurables"])
    assert "Publicación" in fila["campos"] and "Publicación" not in fila["restaurables"]
    r = client.post(f"/store/history/{fila['id']}/restore")
    assert r.status_code == 200, r.text
    st = client.get("/store").json()
    assert st["tagline"] == "Frase original"  # lo restaurable vuelve
    assert st["published"] is False and st["currency"] == "USD" and st["domain"] == "tienda.ejemplo.com"  # lo demas no se toca
    db_session.expire_all()
    assert ((db_session.get(Tenant, tid).settings or {}).get("catalog_v") or 0) > v0  # la tienda lo ve en la siguiente visita
    # el restaurar queda tambien en el historial y sube la version (una pestana abierta antes recibe 409)
    assert client.get("/store/history").json()[0]["restaurado_de"] == fila["id"]


def test_restaurar_portada_y_permisos(client, auth, db_session):
    assert client.put("/store/showcase", json={"headline": "Titular uno"}).status_code == 200
    assert client.put("/store/showcase", json={"headline": "Titular dos"}).status_code == 200
    fila = next(x for x in client.get("/store/history").json() if x["seccion"] == "showcase")
    assert client.post(f"/store/history/{fila['id']}/restore").status_code == 200
    assert client.get("/store/showcase").json()["headline"] == "Titular uno"
    assert client.post("/store/history/999999/restore").status_code == 404
    pw = _user_with_role(db_session, auth["tenant"]["id"], "inventario.hist@ejemplo.com", "inventario")
    keep = client.headers.pop("Authorization")
    tok = client.post("/auth/login", json={"email": "inventario.hist@ejemplo.com", "password": pw}).json()["access_token"]
    client.headers["Authorization"] = keep
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/store/history", headers=h).status_code == 403
    assert client.post(f"/store/history/{fila['id']}/restore", headers=h).status_code == 403


def test_un_valor_con_numero_largo_no_se_ofrece_para_restaurar(client, auth, db_session):
    assert client.put("/store", json={"tagline": "Llámenos al 8888-7777"}).status_code == 200
    assert client.put("/store", json={"tagline": "Frase sin número"}).status_code == 200
    fila = next(x for x in client.get("/store/history").json() if x["seccion"] == "store")
    assert "Frase" in fila["campos"] and "Frase" not in fila["restaurables"]
    assert client.post(f"/store/history/{fila['id']}/restore").status_code == 422
