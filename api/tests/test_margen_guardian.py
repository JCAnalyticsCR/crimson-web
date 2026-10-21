"""Guardian de margen (plan 1.12): reproduce el caso de negocio (costo 100 -> 130 USD y tipo de cambio +10 %).

- La primera corrida es solo informe (no oculta nada); despues queda activo.
- En activo, lo que queda bajo el margen minimo (25 % por defecto) pasa a "Consultar" en la tienda y el checkout ya no
  lo vende; entra a Aprobaciones -> "Precios por revisar" con el precio sugerido = costo x TC / (1 - objetivo),
  redondeado a la centena hacia arriba. Un salto de costo de 25 % o mas se marca "dudoso".
- NUNCA reprecia solo: "Aplicar" (un clic) cambia el precio y lo vuelve a publicar.
- Corre despues de cada refresco de Eurocomp y de cada tasa nueva; correrlo dos veces no cambia nada (idempotente).
- Tope por corrida, liberacion automatica al recuperar el margen y resumen diario al admin (una vez por dia)."""

from datetime import date
from decimal import Decimal

from sqlalchemy import delete, select

from app.models import AuditLog, EmailOutbox, ExchangeRate, Product, ProductTax, Tax, Tenant
from app.services import margin_guard
from app.tasks import margen as tareas
from tests.test_aprobaciones import _sec
from tests.test_inventario_reservas import _entrada, _login_role, _wh
from tests.test_supplier_sync import eurocomp  # noqa: F401  (fixture: Eurocomp falso, sin red)
from tests.test_tienda_checkout import SLUG, _body, _shop

HOY = date.today()


def _producto(client, db, tid, code="HIK-QA", price="78900", cost="100", **kw) -> Product:
    """Producto de la tienda como lo crea el catalogo de Eurocomp: precio en colones con margen 35 % sobre 100 USD."""
    iva = db.scalar(select(Tax).where(Tax.tenant_id == tid, Tax.rate_code == "08"))
    p = Product(
        tenant_id=tid,
        code=code,
        name=kw.pop("name", "Cámara IP 4MP domo"),
        price=Decimal(price),
        currency="CRC",
        cost=Decimal(cost),
        cost_currency="USD",
        margin_pct=Decimal(35),
        show_on_web=True,
        active=True,
        **kw,
    )
    p.taxes.append(ProductTax(tax_id=iva.id))
    db.add(p)
    db.commit()
    _entrada(client, p.id, _wh(client)["id"], 5)
    return p


def _set_cost(db, p: Product, cost) -> None:
    p.cost = Decimal(str(cost))
    db.commit()


def _pub(client, pid) -> dict:
    return next(x for x in client.get(f"{SLUG}/products").json() if x["id"] == pid)


def _venta(client, pid):
    return client.post(f"{SLUG}/checkout", json=_body(pid, lines=[{"product_id": pid, "quantity": 1}]))


def _settings(db, tid) -> dict:
    return dict(db.get(Tenant, tid).settings or {})


def test_caso_de_negocio_informe_activo_dudoso_y_aplicar(client, auth, db_session):
    tid = auth["tenant"]["id"]
    _shop(client)
    p = _producto(client, db_session, tid)

    # primera corrida (tasa nueva): margen 35 % -> nada que hacer; queda en informe solo esa vez y pasa a activo
    assert client.put("/fx", json={"date": HOY.isoformat(), "currency": "USD", "sell": 512.35, "buy": 505.10}).status_code == 200
    st = _settings(db_session, tid)
    assert st["margin_guard_mode"] == "activo" and st["margin_guard_last"]["mode"] == "informe" and st["margin_guard_last"]["evaluados"] == 1
    db_session.refresh(p)
    assert not p.margin_hold and p.margin_flagged_at is None and p.margin_info["margin_pct"] == "35.1"

    # Eurocomp sube el costo 100 -> 130 USD: margen 15,6 % -> fuera de venta, dudoso (salto de 30 %)
    _set_cost(db_session, p, 130)
    out = margin_guard.run_all(db_session, reason="costos de Eurocomp")[tid]
    assert out["mode"] == "activo" and out["bajo_minimo"] == 1 and out["nuevos_retenidos"] == 1 and out["dudosos"] == 1
    db_session.refresh(p)
    assert p.margin_hold and p.margin_flagged_at is not None
    assert p.margin_info["margin_pct"] == "15.6" and p.margin_info["dudoso"] is True and p.margin_info["jump_pct"] == "30"
    assert p.margin_info["suggested"] == "102500"  # 130 x 512,35 / 0,65 = 102 470 -> centena hacia arriba
    assert p.price == Decimal("78900")  # nunca reprecia solo

    # la tienda lo muestra "Consultar" y el checkout ya no lo vende bajo el 25 %
    pub = _pub(client, p.id)
    assert pub["price_on_request"] is True and Decimal(str(pub["display_price"])) == 0
    r = _venta(client, p.id)
    assert r.status_code == 409 and "precio a consultar" in r.json()["detail"]

    # el tipo de cambio sube 10 %: sigue fuera, con el sugerido nuevo; correr dos veces no cambia nada
    flagged_at = p.margin_flagged_at
    assert client.put("/fx", json={"date": HOY.isoformat(), "currency": "USD", "sell": 563.58, "buy": 556.0}).status_code == 200
    margin_guard.run_all(db_session)
    db_session.refresh(p)
    assert p.margin_hold and p.margin_flagged_at == flagged_at and p.margin_info["margin_pct"] == "7.1"
    assert p.margin_info["suggested"] == "112800" and p.margin_info["dudoso"] is True  # 130 x 563,58 / 0,65 = 112 716

    # Aprobaciones -> Precios por revisar: el sugerido, la marca y el boton "Aplicar"
    sec = _sec(client, "precios_por_revisar")
    it = next(i for i in sec["items"] if i["id"] == p.id)
    assert sec["count"] >= 1 and Decimal(str(it["amount"])) == Decimal(112800) and it["currency"] == "CRC"
    assert "Fuera de venta" in it["badge"] and "dudoso" in it["badge"] and "Margen 7,1 %" in it["subtitle"]
    assert it["action"]["path"] == f"/approvals/prices/{p.id}/apply"

    # aplicar el sugerido lo vuelve a publicar
    r = client.post(it["action"]["path"])
    assert r.status_code == 200, r.text
    assert Decimal(str(r.json()["price"])) == Decimal(112800) and r.json()["held"] is False
    db_session.refresh(p)
    assert p.price == Decimal(112800) and not p.margin_hold and p.margin_flagged_at is None and p.margin_pct == Decimal(35)
    assert p.margin_info["dudoso"] is False and p.margin_info["cost_ref"] == "130"
    log = db_session.scalar(select(AuditLog).where(AuditLog.action == "margin_apply", AuditLog.entity_id == p.id))
    assert log and log.before["price"] == "78900" and log.after["price"] == "112800"
    assert p.id not in [i["id"] for i in _sec(client, "precios_por_revisar")["items"]]
    pub = _pub(client, p.id)
    assert pub["price_on_request"] is False and Decimal(str(pub["display_price"])) == Decimal("127464.00")  # + IVA
    assert _venta(client, p.id).status_code == 201

    # el costo ya revisado es la nueva referencia: la siguiente corrida no lo vuelve a sacar ni lo marca dudoso
    margin_guard.run_all(db_session)
    db_session.refresh(p)
    assert not p.margin_hold and p.margin_flagged_at is None and p.margin_info["dudoso"] is False


def test_modo_informe_no_oculta_y_el_margen_recuperado_vuelve_solo(client, auth, db_session):
    tid = auth["tenant"]["id"]
    _shop(client)
    p = _producto(client, db_session, tid, code="HIK-INF")
    assert client.put("/settings", json={"margin_guard_mode": "informe"}).status_code == 200
    _set_cost(db_session, p, 120)
    margin_guard.run_all(db_session)
    db_session.refresh(p)
    assert p.margin_flagged_at is not None and not p.margin_hold  # en informe: a revision, pero sigue a la venta
    assert _settings(db_session, tid)["margin_guard_mode"] == "informe"  # un informe elegido no se cambia solo
    assert _pub(client, p.id)["price_on_request"] is False and _venta(client, p.id).status_code == 201
    it = next(i for i in _sec(client, "precios_por_revisar")["items"] if i["id"] == p.id)
    assert it["badge"].startswith("Solo informe")

    # pasar a activo desde Ajustes revisa ya: sale de venta
    r = client.put("/settings", json={"margin_guard_mode": "activo"})
    assert r.status_code == 200 and r.json()["margin_guard_last"]["nuevos_retenidos"] == 1
    db_session.refresh(p)
    assert p.margin_hold and _pub(client, p.id)["price_on_request"] is True

    # el costo vuelve a lo normal: el margen se recupera y vuelve solo a la venta, sin tocar el precio
    _set_cost(db_session, p, 95)
    out = margin_guard.run_all(db_session)[tid]
    db_session.refresh(p)
    assert out["liberados"] == 1 and not p.margin_hold and p.margin_flagged_at is None and p.price == Decimal(78900)
    assert _pub(client, p.id)["price_on_request"] is False

    # bajar el minimo tambien re-evalua: un 20 % de margen ya no es problema con minimo 15 %
    _set_cost(db_session, p, 123)  # (78900 - 63019) / 78900 = 20,1 %
    margin_guard.run_all(db_session)
    db_session.refresh(p)
    assert p.margin_hold
    assert client.put("/settings", json={"min_margin_pct": 15}).status_code == 200
    db_session.refresh(p)
    assert not p.margin_hold and p.margin_flagged_at is None


def test_tope_por_corrida_saca_primero_el_peor_margen(client, auth, db_session):
    tid = auth["tenant"]["id"]
    _shop(client)
    a = _producto(client, db_session, tid, code="TOPE-A", name="Equipo A")
    b = _producto(client, db_session, tid, code="TOPE-B", name="Equipo B")
    t = db_session.get(Tenant, tid)
    t.settings = {**(t.settings or {}), "margin_guard_mode": "activo", "margin_guard_cap": 1}
    db_session.commit()
    _set_cost(db_session, a, 120)  # 22 %
    _set_cost(db_session, b, 140)  # 9 %: peor, sale primero
    out = margin_guard.run_all(db_session)[tid]
    assert out["nuevos_retenidos"] == 1 and out["sobre_tope"] == 1
    db_session.refresh(a)
    db_session.refresh(b)
    assert b.margin_hold and not a.margin_hold and a.margin_info["over_cap"] is True
    it = next(i for i in _sec(client, "precios_por_revisar")["items"] if i["id"] == a.id)
    assert it["badge"].startswith("Sobre el tope")
    # la corrida siguiente toma el que quedo pendiente
    margin_guard.run_all(db_session)
    db_session.refresh(a)
    assert a.margin_hold


def test_sin_tasa_no_evalua_ni_saca_de_venta_y_lo_despublicado_sale_de_revision(client, auth, db_session):
    tid = auth["tenant"]["id"]
    _shop(client)
    p = _producto(client, db_session, tid, code="SIN-TC", cost="140")
    t = db_session.get(Tenant, tid)
    t.settings = {**(t.settings or {}), "margin_guard_mode": "activo"}
    db_session.commit()
    db_session.execute(delete(ExchangeRate))
    db_session.commit()
    out = margin_guard.run_all(db_session)[tid]
    db_session.refresh(p)
    assert out["sin_tasa"] >= 1 and out["evaluados"] == 0 and not p.margin_hold
    # sin tasa, un producto con precio en colones se sigue vendiendo (la tienda no se cae)
    assert _venta(client, p.id).status_code == 201
    # sin tasa, aplicar un precio da un 422 claro (nunca convierte con 1)
    db_session.add(ExchangeRate(date=HOY, currency="USD", sell=Decimal("512.35"), buy=Decimal("505.10"), source="manual"))
    db_session.commit()
    margin_guard.run_all(db_session)
    db_session.refresh(p)
    assert p.margin_hold
    db_session.execute(delete(ExchangeRate))
    db_session.commit()
    r = client.post(f"/approvals/prices/{p.id}/apply")
    assert r.status_code == 422 and "tipo de cambio" in r.json()["detail"]
    # despublicado: no hay nada que proteger, sale de la revision
    p.show_on_web = False
    db_session.commit()
    margin_guard.run_all(db_session)
    db_session.refresh(p)
    assert not p.margin_hold and p.margin_flagged_at is None


def test_refresco_de_eurocomp_corre_el_guardian(client, auth, db_session, eurocomp):  # noqa: F811
    from app.services import supplier_sync as ss
    from tests.test_supplier_sync import CAMARA

    tid = auth["tenant"]["id"]
    _shop(client)
    p = _producto(client, db_session, tid, code="HIK-1047", price="38000", cost="48.5", supplier_sku="SG2104")
    t = db_session.get(Tenant, tid)
    t.settings = {**(t.settings or {}), "margin_guard_mode": "activo"}
    db_session.commit()
    ss.sync_all(db_session)  # vincula el articulo (costo 48,50 USD: margen 34,6 %)
    db_session.refresh(p)
    assert not p.margin_hold
    eurocomp["items"] = [dict(CAMARA, precio="65.00")]  # +34 %: margen 12,4 %
    ss.refresh_linked(db_session)
    db_session.refresh(p)
    assert p.cost == Decimal(65) and p.margin_hold and p.margin_info["dudoso"] is True
    assert p.price == Decimal(38000)
    assert _settings(db_session, tid)["margin_guard_last"]["reason"] == "costos de Eurocomp"


def test_solo_quien_ve_costos_revisa_y_aplica(client, auth, db_session):
    tid = auth["tenant"]["id"]
    _shop(client)
    p = _producto(client, db_session, tid, code="PERM-1", cost="140")
    t = db_session.get(Tenant, tid)
    t.settings = {**(t.settings or {}), "margin_guard_mode": "activo"}
    db_session.commit()
    margin_guard.run_all(db_session)
    for rol in ("ventas", "supervisor", "inventario"):
        h = _login_role(client, db_session, tid, f"{rol}-margen@ejemplo.com", rol)
        assert client.post(f"/approvals/prices/{p.id}/apply", headers=h).status_code == 403, rol
        assert client.post("/approvals/prices/run", headers=h).status_code == 403, rol
    db_session.refresh(p)
    assert p.margin_hold and p.price == Decimal(78900)
    r = client.post("/approvals/prices/run")
    assert r.status_code == 200 and r.json()["retenidos"] >= 1


def test_resumen_diario_al_admin_una_vez_por_dia(client, auth, db_session):
    tid = auth["tenant"]["id"]
    _shop(client)
    assert tareas.daily_summary(db_session) == 0  # nada bajo el minimo: no hay correo
    p = _producto(client, db_session, tid, code="RES-1", cost="140")
    t = db_session.get(Tenant, tid)
    t.settings = {**(t.settings or {}), "margin_guard_mode": "activo"}
    db_session.commit()
    margin_guard.run_all(db_session)
    antes = db_session.scalar(select(EmailOutbox.id).order_by(EmailOutbox.id.desc())) or 0
    assert tareas.daily_summary(db_session) == 1
    assert tareas.daily_summary(db_session) == 0  # el beat dispara dos veces: un solo correo
    correos = db_session.scalars(select(EmailOutbox).where(EmailOutbox.id > antes)).all()
    assert correos and all(c.subject.startswith("Margen: 1 producto(s) bajo el 25") for c in correos)
    assert p.name in correos[0].html and "Precios por revisar" in correos[0].html and "nunca cambia un precio solo" in correos[0].html


def test_editar_a_mano_un_precio_retenido_lo_libera_ya(client, auth, db_session):
    """Alguien corrige el precio en Productos en lugar de "Aplicar": si ya cumple el minimo vuelve a la venta en el
    acto (sin esperar al proximo refresco); si sigue bajo, sigue fuera con el sugerido al dia. Editar nunca saca de
    venta por si solo y nunca reprecia."""
    tid = auth["tenant"]["id"]
    _shop(client)
    p = _producto(client, db_session, tid, code="EDIT-1", cost="140")
    t = db_session.get(Tenant, tid)
    t.settings = {**(t.settings or {}), "margin_guard_mode": "activo"}
    db_session.commit()
    margin_guard.run_all(db_session)
    db_session.refresh(p)
    assert p.margin_hold
    ficha = next(x for x in client.get("/products", params={"q": "EDIT-1"}).json()["items"] if x["id"] == p.id)
    assert ficha["margin_hold"] is True
    base = {k: ficha[k] for k in ("code", "name", "currency", "cost", "cost_currency") if k in ficha}
    # sube el precio pero no alcanza (140 x 512,35 = 71 729 de costo; 85 000 da 15,6 %): sigue fuera
    r = client.put(f"/products/{p.id}", json={**base, "price": 85000})
    assert r.status_code == 200, r.text
    db_session.refresh(p)
    assert p.margin_hold and p.margin_info["margin_pct"] == "15.6" and p.price == Decimal(85000)
    # con 110 000 el margen es 34,8 %: vuelve a la venta ya y sale de la revision
    r = client.put(f"/products/{p.id}", json={**base, "price": 110000})
    assert r.status_code == 200 and r.json()["margin_hold"] is False
    db_session.refresh(p)
    assert not p.margin_hold and p.margin_flagged_at is None and p.price == Decimal(110000)
    assert _pub(client, p.id)["price_on_request"] is False
    assert p.id not in [i["id"] for i in _sec(client, "precios_por_revisar")["items"]]


def test_despublicar_desde_la_lista_saca_de_la_revision(client, auth, db_session):
    tid = auth["tenant"]["id"]
    _shop(client)
    p = _producto(client, db_session, tid, code="WEB-1", cost="140")
    t = db_session.get(Tenant, tid)
    t.settings = {**(t.settings or {}), "margin_guard_mode": "activo"}
    db_session.commit()
    margin_guard.run_all(db_session)
    assert client.patch(f"/products/{p.id}/web", json={"show_on_web": False}).status_code == 200
    db_session.refresh(p)
    assert not p.margin_hold and p.margin_flagged_at is None


def test_resumen_avisa_tipo_de_cambio_viejo_o_faltante(client, auth, db_session):
    """BCCR_TOKEN vacio: la tasa se carga a mano. Si queda vieja (mas de 2 dias habiles) o falta y la tienda tiene
    productos con costo o precio en dolares, el resumen diario lo avisa aunque no haya nada bajo el minimo."""
    tid = auth["tenant"]["id"]
    _shop(client)
    _producto(client, db_session, tid, code="FX-VIEJO")
    db_session.execute(delete(ExchangeRate))
    db_session.add(ExchangeRate(date=date(2026, 1, 5), currency="USD", sell=Decimal("512.35"), buy=Decimal("505.10"), source="manual"))
    db_session.commit()
    antes = db_session.scalar(select(EmailOutbox.id).order_by(EmailOutbox.id.desc())) or 0
    assert tareas.daily_summary(db_session) == 1
    c = db_session.scalars(select(EmailOutbox).where(EmailOutbox.id > antes)).first()
    assert c.subject.startswith("Tipo de cambio viejo o faltante") and "05/01/2026" in c.html and "Manual" in c.html
    # sin ninguna tasa: tambien avisa (al dia siguiente; hoy ya salio)
    t = db_session.get(Tenant, tid)
    t.settings = {k: v for k, v in (t.settings or {}).items() if k != "margin_guard_summary_on"}
    db_session.execute(delete(ExchangeRate))
    db_session.commit()
    assert tareas.daily_summary(db_session) == 1
    c = db_session.scalars(select(EmailOutbox).where(EmailOutbox.id > antes).order_by(EmailOutbox.id.desc())).first()
    assert "No hay tipo de cambio de USD" in c.html
    assert margin_guard.fx_alerts(db_session, t)


def test_red_diaria_corre_el_guardian_antes_del_resumen(client, auth, db_session):
    """La unica entrada del beat (margin.daily_summary) corre primero el guardian: sin BCCR ni Eurocomp nadie mas lo
    dispara y una edicion de costo hecha a mano igual queda protegida al dia siguiente."""
    tid = auth["tenant"]["id"]
    _shop(client)
    p = _producto(client, db_session, tid, code="RED-1")
    t = db_session.get(Tenant, tid)
    t.settings = {**(t.settings or {}), "margin_guard_mode": "activo"}
    db_session.commit()
    _set_cost(db_session, p, 140)  # nadie corrio el guardian todavia
    out = tareas.guard_all(db_session)
    assert out[tid]["nuevos_retenidos"] == 1 and out[tid]["reason"] == "revisión diaria"
    db_session.refresh(p)
    assert p.margin_hold
    assert tareas.daily_summary(db_session) == 1


def test_umbral_de_salto_dudoso_configurable(client, auth, db_session):
    """El 25 % del salto "dudoso" es el default (Andres confirma el umbral): con 40 % un salto de 30 % ya no se marca."""
    tid = auth["tenant"]["id"]
    _shop(client)
    p = _producto(client, db_session, tid, code="JUMP-1")
    assert client.put("/settings", json={"margin_guard_mode": "activo", "margin_guard_jump_pct": 40}).status_code == 200
    assert client.get("/settings").json()["margin_guard_jump_pct"] == 40
    _set_cost(db_session, p, 130)  # +30 %
    margin_guard.run_all(db_session)
    db_session.refresh(p)
    assert p.margin_hold and p.margin_info["jump_pct"] == "30" and p.margin_info["dudoso"] is False
    assert client.put("/settings", json={"margin_guard_jump_pct": 0}).status_code == 422
