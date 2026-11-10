"""Margen por categoria (Catalogo -> Margenes, 10/11).

- Prioridad: margen propio del producto -> categoria mas cercana (subiendo por los padres) -> general (Ajustes).
- La vista previa no cambia nada; dice cuantos productos cambian y el precio antes y despues.
- Los precios cambian solo al confirmar la MISMA vista previa (token); si algo cambio, 409 y nada se toca.
- Los productos con margen propio se respetan salvo que el admin los incluya (dejan de tener margen propio).
- Una subcategoria con su propio margen manda sobre la de arriba.
- Quitar el margen de la categoria no cambia precios. La vendedora no ve ni usa la pantalla.
Datos ficticios, costos en colones y precios de 2 000 o menos."""

from decimal import Decimal

from sqlalchemy import select

from app.models import AuditLog, Category, Product, Tenant
from app.services import category_margin as cm
from app.services import margin_guard
from tests.test_papelera2 import _login_as


def _cat(db, tid, name, parent=None, margin=None):
    c = Category(tenant_id=tid, name=name, parent_id=parent.id if parent else None, margin_pct=margin)
    db.add(c)
    db.flush()
    return c


def _prod(db, tid, code, cat, cost, price, margin=None, web_price=None):
    p = Product(
        tenant_id=tid, name=f"Prueba {code}", code=code, price=Decimal(price), currency="CRC", cost=Decimal(cost),
        cost_currency="CRC", margin_pct=margin, category_id=cat.id if cat else None, web_price=web_price, show_on_web=True,
    )  # fmt: skip
    db.add(p)
    db.flush()
    return p


def _base(db, tid):
    audio = _cat(db, tid, "Audio")
    parl = _cat(db, tid, "Parlantes", audio)
    micro = _cat(db, tid, "Micrófonos", audio, margin=Decimal(50))
    a = _prod(db, tid, "AUD-1", audio, 650, 900)  # hereda
    b = _prod(db, tid, "AUD-2", parl, 1300, 1900)  # hereda por la subcategoria sin margen
    c = _prod(db, tid, "AUD-3", audio, 700, 1100, margin=Decimal(40))  # margen propio
    m = _prod(db, tid, "AUD-4", micro, 500, 1000)  # manda Micrófonos (50 %)
    s = _prod(db, tid, "AUD-5", audio, 0, 800)  # sin costo
    s.cost = None
    db.commit()
    return {"audio": audio, "parl": parl, "micro": micro, "a": a, "b": b, "c": c, "m": m, "s": s}


def test_prioridad_producto_categoria_general(db_session, auth):
    tid = auth["tenant"]["id"]
    x = _base(db_session, tid)
    t = db_session.get(Tenant, tid)
    res = cm.resolve(cm.categories(db_session, tid))
    assert cm.effective(t, x["c"], res)[:2] == (Decimal(40), "producto")
    assert cm.effective(t, x["m"], res)[:2] == (Decimal(50), "categoria")
    assert cm.effective(t, x["b"], res)[:2][1] == "general"  # nadie en la cadena: el general
    x["audio"].margin_pct = Decimal(30)
    db_session.commit()
    res = cm.resolve(cm.categories(db_session, tid))
    m, src, cat = cm.effective(t, x["b"], res)
    assert (m, src, cat) == (Decimal(30), "categoria", x["audio"].id)  # Parlantes hereda de Audio
    # el guardian de margen sugiere con el de la categoria
    cfg = margin_guard.config(t, db_session)
    assert margin_guard._target(t, x["b"], cfg) == max(Decimal(30), cfg["min_pct"])


def test_vista_previa_no_cambia_nada_y_cuenta(client, auth, db_session):
    tid = auth["tenant"]["id"]
    x = _base(db_session, tid)
    r = client.post(f"/catalog/margins/{x['audio'].id}/preview", json={"margin_pct": 35})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 5
    assert body["counts"]["cambian"] == 2  # AUD-1 y AUD-2
    assert body["counts"]["margen_propio"] == 1 and body["counts"]["por_subcategoria"] == 1 and body["counts"]["sin_costo"] == 1
    rows = {r["code"]: r for r in body["items"]}
    # 650 / 0,65 = 1000 ; 1300 / 0,65 = 2000 (centena hacia arriba)
    assert Decimal(str(rows["AUD-1"]["price_after"])) == Decimal(1000)
    assert Decimal(str(rows["AUD-2"]["price_after"])) == Decimal(2000)
    assert rows["AUD-3"]["price_after"] is None and "propio" in rows["AUD-3"]["skip"]
    assert rows["AUD-4"]["price_after"] is None and "Micrófonos" in rows["AUD-4"]["skip"]
    db_session.expire_all()
    assert Decimal(str(db_session.get(Product, x["a"].id).price)) == Decimal(900)
    assert db_session.get(Category, x["audio"].id).margin_pct is None


def test_confirmar_aplica_lo_que_se_vio(client, auth, db_session):
    tid = auth["tenant"]["id"]
    x = _base(db_session, tid)
    prev = client.post(f"/catalog/margins/{x['audio'].id}/preview", json={"margin_pct": 35}).json()
    r = client.post(f"/catalog/margins/{x['audio'].id}/apply", json={"margin_pct": 35, "token": prev["token"]})
    assert r.status_code == 200, r.text
    assert r.json()["changes"] == 2
    db_session.expire_all()
    assert Decimal(str(db_session.get(Category, x["audio"].id).margin_pct)) == Decimal(35)
    assert Decimal(str(db_session.get(Product, x["a"].id).price)) == Decimal(1000)
    assert Decimal(str(db_session.get(Product, x["b"].id).price)) == Decimal(2000)
    assert Decimal(str(db_session.get(Product, x["c"].id).price)) == Decimal(1100)  # margen propio: intacto
    assert Decimal(str(db_session.get(Product, x["m"].id).price)) == Decimal(1000)  # Micrófonos manda
    assert db_session.get(Product, x["a"].id).margin_pct is None  # sigue heredando, no se le estampa margen propio
    log = db_session.scalars(select(AuditLog).where(AuditLog.action == "category_margin", AuditLog.entity_id == x["audio"].id)).all()
    assert len(log) == 1 and log[0].diff["n"] == 2


def test_sin_token_o_con_cambios_no_aplica(client, auth, db_session):
    tid = auth["tenant"]["id"]
    x = _base(db_session, tid)
    assert client.post(f"/catalog/margins/{x['audio'].id}/apply", json={"margin_pct": 35}).status_code == 422
    prev = client.post(f"/catalog/margins/{x['audio'].id}/preview", json={"margin_pct": 35}).json()
    x["a"].cost = Decimal(700)  # cambio el costo despues de la vista previa
    db_session.commit()
    r = client.post(f"/catalog/margins/{x['audio'].id}/apply", json={"margin_pct": 35, "token": prev["token"]})
    assert r.status_code == 409
    db_session.expire_all()
    assert Decimal(str(db_session.get(Product, x["b"].id).price)) == Decimal(1900)
    assert db_session.get(Category, x["audio"].id).margin_pct is None


def test_incluir_margen_propio(client, auth, db_session):
    tid = auth["tenant"]["id"]
    x = _base(db_session, tid)
    prev = client.post(f"/catalog/margins/{x['audio'].id}/preview", json={"margin_pct": 30, "include_own": True}).json()
    assert prev["counts"]["cambian"] == 2 and prev["counts"]["sin_cambio"] == 1 and prev["own_clears"] == 1  # AUD-2 ya da 1 900
    r = client.post(f"/catalog/margins/{x['audio'].id}/apply", json={"margin_pct": 30, "include_own": True, "token": prev["token"]})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    c = db_session.get(Product, x["c"].id)
    assert c.margin_pct is None and Decimal(str(c.price)) == Decimal(1000)  # 700 / 0,70 = 1000


def test_quitar_margen_no_cambia_precios(client, auth, db_session):
    tid = auth["tenant"]["id"]
    x = _base(db_session, tid)
    r = client.delete(f"/catalog/margins/{x['micro'].id}")
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert db_session.get(Category, x["micro"].id).margin_pct is None
    assert Decimal(str(db_session.get(Product, x["m"].id).price)) == Decimal(1000)


def test_resumen_de_categorias(client, auth, db_session):
    tid = auth["tenant"]["id"]
    x = _base(db_session, tid)
    body = client.get("/catalog/margins").json()
    rows = {r["id"]: r for r in body["categories"]}
    a = rows[x["audio"].id]
    assert a["products_tree"] == 5 and a["products"] == 3 and a["own_margin"] == 1 and a["sin_costo"] == 1
    assert a["source"] == "general"
    mi = rows[x["micro"].id]
    assert mi["source"] == "propio" and Decimal(str(mi["effective_pct"])) == Decimal(50)
    lst = client.get(f"/catalog/margins/{x['audio'].id}/products").json()
    src = {i["code"]: i["source"] for i in lst["items"]}
    assert src == {"AUD-1": "general", "AUD-2": "general", "AUD-3": "producto", "AUD-4": "categoria", "AUD-5": "general"}


def test_margen_fuera_de_rango_y_otra_empresa(client, auth, db_session):
    tid = auth["tenant"]["id"]
    x = _base(db_session, tid)
    assert client.post(f"/catalog/margins/{x['audio'].id}/preview", json={"margin_pct": 0}).status_code == 422
    assert client.post(f"/catalog/margins/{x['audio'].id}/preview", json={"margin_pct": 95}).status_code == 422
    assert client.post("/catalog/margins/999999/preview", json={"margin_pct": 30}).status_code == 404


def test_la_vendedora_no_ve_margenes(client, auth, db_session):
    tid = auth["tenant"]["id"]
    x = _base(db_session, tid)
    h = _login_as(client, db_session, tid, "vendedora@ejemplo.com", "ventas")
    assert client.get("/catalog/margins", headers=h).status_code == 403
    assert client.post(f"/catalog/margins/{x['audio'].id}/preview", json={"margin_pct": 30}, headers=h).status_code == 403
    # y la lista de categorias no trae el margen
    cats = client.get("/categories", headers=h).json()
    assert all("margin_pct" not in c for c in cats)
