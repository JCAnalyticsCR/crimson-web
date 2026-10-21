"""Guardian de margen (plan 1.12): reproduce el caso de negocio (costo 100 -> 130 USD y tipo de cambio +10 %).

- La primera corrida es solo informe (no oculta nada); despues queda activo.
- En activo, TODO lo que queda bajo el margen minimo (25 % por defecto) pasa a "Consultar" en la tienda y el checkout
  ya no lo vende; entra a Aprobaciones -> "Precios por revisar" con el precio sugerido = costo x TC / (1 - objetivo),
  redondeado a la centena hacia arriba. Se mide con el menor precio que vende la tienda (base o variante).
- "Dudoso": salto de costo o de tipo de cambio de 25 % o mas, en todo producto (sobre el minimo solo se avisa).
- NUNCA reprecia solo: "Aplicar" aplica exactamente el precio que la persona vio (si el de hoy es otro, 409).
- Freno solo ante una señal de error (tipo de cambio que salto o mayoria dudosa), contado sobre el total fuera de venta.
- Corre despues de cada refresco de Eurocomp, de cada lista de proveedor y de cada tasa nueva (solo la empresa que la
  cargo); correrlo dos veces no cambia nada. Liberacion automatica al recuperar el margen, despublicar no libera, y
  resumen diario al admin (una vez por dia, sin BCC)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select, update

from app.models import AuditLog, EmailOutbox, ExchangeRate, Product, ProductTax, ProductVariant, Tax, Tenant
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
    p.cost = Decimal(str(cost)) if cost is not None else None
    db.commit()


def _pub(client, pid) -> dict:
    return next(x for x in client.get(f"{SLUG}/products").json() if x["id"] == pid)


def _venta(client, pid):
    return client.post(f"{SLUG}/checkout", json=_body(pid, lines=[{"product_id": pid, "quantity": 1}]))


def _settings(db, tid) -> dict:
    return dict(db.get(Tenant, tid).settings or {})


def _activo(db, tid, **extra) -> None:
    t = db.get(Tenant, tid)
    t.settings = {**(t.settings or {}), "margin_guard_mode": "activo", **extra}
    db.commit()


def _item(client, pid) -> dict:
    return next(i for i in _sec(client, "precios_por_revisar")["items"] if i["id"] == pid)


def _aplicar(client, it, **kw):
    return client.post(it["action"]["path"], json=it["action"]["body"], **kw)


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
    assert not out.get("frenada")  # un solo producto: ningun freno
    db_session.refresh(p)
    assert p.margin_hold and p.margin_flagged_at is not None
    assert p.margin_info["margin_pct"] == "15.6" and p.margin_info["dudoso"] is True and p.margin_info["jump_pct"] == "30"
    assert p.margin_info["dudoso_por"] == ["costo"]
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

    # Aprobaciones -> Precios por revisar: el sugerido, la marca y el boton "Aplicar" con el precio que se muestra
    sec = _sec(client, "precios_por_revisar")
    it = next(i for i in sec["items"] if i["id"] == p.id)
    assert sec["count"] >= 1 and Decimal(str(it["amount"])) == Decimal(112800) and it["currency"] == "CRC"
    assert "Fuera de venta" in it["badge"] and "dudoso" in it["badge"] and "Margen 7,1 %" in it["subtitle"]
    assert it["action"]["path"] == f"/approvals/prices/{p.id}/apply" and Decimal(str(it["action"]["body"]["price"])) == Decimal(112800)
    assert it["to"] == f"/productos?q=HIK-QA&ficha={p.id}"  # abre la ficha para verificar el costo

    # aplicar el sugerido lo vuelve a publicar
    r = _aplicar(client, it)
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

    # doble clic: el segundo no hace nada (ya salio de la revision)
    r = _aplicar(client, it)
    assert r.status_code == 409 and "ya no está" in r.json()["detail"]

    # el costo ya revisado es la nueva referencia: la siguiente corrida no lo vuelve a sacar ni lo marca dudoso
    margin_guard.run_all(db_session)
    db_session.refresh(p)
    assert not p.margin_hold and p.margin_flagged_at is None and p.margin_info["dudoso"] is False


def test_aplicar_solo_el_precio_que_se_vio(client, auth, db_session):
    """ "Aplicar" nunca cambia a un monto distinto del confirmado: si el producto ya salio de la revision (el costo bajo y
    la corrida lo libero) o el sugerido de hoy es otro (cambio el costo sin corrida), responde 409 y no toca el precio."""
    tid = auth["tenant"]["id"]
    _shop(client)
    p = _producto(client, db_session, tid, code="APL-1", cost="130")
    otro = _producto(client, db_session, tid, code="APL-2", cost="50")  # 67 %: nunca estuvo en revision
    _activo(db_session, tid)
    margin_guard.run_all(db_session)
    it = _item(client, p.id)
    assert Decimal(str(it["action"]["body"]["price"])) == Decimal(102500)

    # (a) el costo bajo y la corrida ya lo libero; con la pagina abierta, el clic no BAJA el precio
    _set_cost(db_session, p, 95)
    margin_guard.run_all(db_session)
    r = _aplicar(client, it)
    assert r.status_code == 409 and "ya no está" in r.json()["detail"]
    db_session.refresh(p)
    assert p.price == Decimal(78900) and not p.margin_hold

    # (b) el costo sube sin que corra el guardian: el sugerido de hoy es otro -> 409 con el nuevo y nada cambia
    _set_cost(db_session, p, 130)
    margin_guard.run_all(db_session)
    it = _item(client, p.id)
    _set_cost(db_session, p, 140)  # una lista que no corrio el guardian (o el TC cambio con la pagina abierta)
    r = _aplicar(client, it)
    assert r.status_code == 409 and "₡110 400" in r.json()["detail"], r.text  # 140 x 512,35 / 0,65 = 110 353
    db_session.refresh(p)
    assert p.price == Decimal(78900) and p.margin_hold
    it = _item(client, p.id)  # la bandeja ya muestra el sugerido de hoy
    assert Decimal(str(it["action"]["body"]["price"])) == Decimal(110400)
    assert _aplicar(client, it).status_code == 200
    db_session.refresh(p)
    assert p.price == Decimal(110400) and not p.margin_hold

    # (c) un producto que nunca estuvo en revision no se reprecia por la API
    r = client.post(f"/approvals/prices/{otro.id}/apply", json={"price": 1000})
    assert r.status_code == 409
    db_session.refresh(otro)
    assert otro.price == Decimal(78900)
    # sin el precio confirmado no hay "Aplicar"
    assert client.post(f"/approvals/prices/{p.id}/apply").status_code == 422


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
    assert _item(client, p.id)["badge"].startswith("Solo informe")

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


def test_sin_senal_de_error_no_hay_tope(client, auth, db_session):
    """El tope NO limita una subida real: sin un tipo de cambio que salto ni una mayoria de costos dudosos, sale de
    venta todo lo que queda bajo el minimo aunque pase el tope (antes del 21 en adelante se seguian vendiendo)."""
    tid = auth["tenant"]["id"]
    _shop(client)
    a = _producto(client, db_session, tid, code="TOPE-A", name="Equipo A")
    b = _producto(client, db_session, tid, code="TOPE-B", name="Equipo B")
    _activo(db_session, tid, margin_guard_cap=1)
    _set_cost(db_session, a, 120)  # +20 %: 22 %, no dudoso
    _set_cost(db_session, b, 140)  # +40 %: 9 %, dudoso (1 de 2: no es mayoria)
    out = margin_guard.run_all(db_session)[tid]
    assert out["nuevos_retenidos"] == 2 and out["frenados"] == 0 and not out.get("frenada")
    db_session.refresh(a)
    db_session.refresh(b)
    assert a.margin_hold and b.margin_hold


def test_freno_ante_un_tipo_de_cambio_mal_digitado(client, auth, db_session):
    """BCCR_TOKEN vacio: la tasa se digita a mano. 5123,5 en vez de 512,35 deja todo lo que cuesta en dolares bajo el
    minimo. El freno no deja mas que el tope fuera de venta EN TOTAL (varias corridas no lo suman), avisa al admin en el
    acto (una vez por dia, sin BCC) y una persona lo destraba (o corrige la tasa y se libera solo)."""
    tid = auth["tenant"]["id"]
    _shop(client)
    db_session.execute(delete(ExchangeRate))
    db_session.add(ExchangeRate(date=HOY - timedelta(days=1), currency="USD", sell=Decimal("512.35"), buy=Decimal("505.10"), source="manual"))
    db_session.commit()
    t = db_session.get(Tenant, tid)
    t.settings = {**(t.settings or {}), "margin_guard_mode": "activo", "margin_guard_cap": 2, "bcc": ["contador@ejemplo.com"]}
    db_session.commit()
    ps = [_producto(client, db_session, tid, code=f"FRENO-{i}", name=f"Equipo {i}", cost=str(100 + i)) for i in range(3)]
    antes = db_session.scalar(select(EmailOutbox.id).order_by(EmailOutbox.id.desc())) or 0

    r = client.put("/fx", json={"date": HOY.isoformat(), "currency": "USD", "sell": 5123.5, "buy": 5051.0})
    assert r.status_code == 200
    last = _settings(db_session, tid)["margin_guard_last"]
    assert last["nuevos_retenidos"] == 2 and last["frenados"] == 1 and "tipo de cambio de USD" in last["frenada"]
    for p in ps:
        db_session.refresh(p)
    assert [p.margin_hold for p in ps] == [False, True, True]  # primero los de peor margen (los de mayor costo)
    assert ps[0].margin_info["over_cap"] is True and "tipo_de_cambio" in ps[0].margin_info["dudoso_por"]
    assert _pub(client, ps[0].id)["price_on_request"] is False  # frenado: sigue a la venta (posible error del dato)
    avisos = db_session.scalars(select(EmailOutbox).where(EmailOutbox.id > antes, EmailOutbox.entity == "margin_summary")).all()
    assert avisos and all(a.bcc is None for a in avisos) and "revisión frenada" in avisos[0].subject

    # otra corrida no suma: ya hay 2 fuera de venta (el tope es del total) y el aviso no se repite hoy
    out = margin_guard.run_all(db_session)[tid]
    assert out["nuevos_retenidos"] == 0 and out["frenados"] == 1 and out["retenidos"] == 2
    assert len(db_session.scalars(select(EmailOutbox).where(EmailOutbox.id > antes, EmailOutbox.entity == "margin_summary")).all()) == len(avisos)
    sec = _sec(client, "precios_por_revisar")
    assert sec["brake"] and sec["brake"]["frenados"] == 1
    assert _item(client, ps[0].id)["badge"].startswith("Frenado")

    # una persona verifico el dato: "Sacar de venta todo"
    r = client.post("/approvals/prices/run", json={"confirmar": True})
    assert r.status_code == 200 and r.json()["nuevos_retenidos"] == 1 and not r.json().get("frenada")
    db_session.refresh(ps[0])
    assert ps[0].margin_hold

    # el dato estaba mal: se corrige la tasa y todo vuelve solo a la venta, sin tocar precios
    assert client.put("/fx", json={"date": HOY.isoformat(), "currency": "USD", "sell": 512.35, "buy": 505.10}).status_code == 200
    for p in ps:
        db_session.refresh(p)
    assert not any(p.margin_hold for p in ps) and all(p.price == Decimal(78900) for p in ps)


def test_sin_tasa_no_evalua_ni_saca_de_venta_y_lo_despublicado_sale_de_la_bandeja(client, auth, db_session):
    tid = auth["tenant"]["id"]
    _shop(client)
    p = _producto(client, db_session, tid, code="SIN-TC", cost="140")
    _activo(db_session, tid)
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
    it = _item(client, p.id)
    db_session.execute(delete(ExchangeRate))
    db_session.commit()
    r = _aplicar(client, it)
    assert r.status_code == 422 and "tipo de cambio" in r.json()["detail"]
    # despublicado: sale de la bandeja (la tienda no lo muestra) pero NO se libera
    p.show_on_web = False
    db_session.commit()
    margin_guard.run_all(db_session)
    db_session.refresh(p)
    assert p.margin_hold and p.margin_flagged_at is not None
    assert p.id not in [i["id"] for i in _sec(client, "precios_por_revisar")["items"]]


def test_despublicar_y_volver_a_publicar_no_lo_vuelve_a_vender(client, auth, db_session):
    """Inventario edita el catalogo pero no ve costos: despublicar y volver a publicar no puede saltarse el "Aplicar"
    de quien ve costos (antes lo liberaba y volvia al checkout bajo el minimo, y desaparecia de Aprobaciones)."""
    tid = auth["tenant"]["id"]
    _shop(client)
    p = _producto(client, db_session, tid, code="WEB-1", cost="140")
    _activo(db_session, tid)
    margin_guard.run_all(db_session)
    h = _login_role(client, db_session, tid, "inventario-margen@ejemplo.com", "inventario")
    assert client.patch(f"/products/{p.id}/web", json={"show_on_web": False}, headers=h).status_code == 200
    db_session.refresh(p)
    assert p.margin_hold and p.id not in [i["id"] for i in _sec(client, "precios_por_revisar")["items"]]
    assert client.patch(f"/products/{p.id}/web", json={"show_on_web": True}, headers=h).status_code == 200
    db_session.refresh(p)
    assert p.margin_hold and p.margin_flagged_at is not None
    r = _venta(client, p.id)
    assert r.status_code == 409 and "precio a consultar" in r.json()["detail"]
    assert p.id in [i["id"] for i in _sec(client, "precios_por_revisar")["items"]]


def test_refresco_de_eurocomp_corre_el_guardian(client, auth, db_session, eurocomp):  # noqa: F811
    from app.services import supplier_sync as ss
    from tests.test_supplier_sync import CAMARA

    tid = auth["tenant"]["id"]
    _shop(client)
    p = _producto(client, db_session, tid, code="HIK-1047", price="38000", cost="48.5", supplier_sku="SG2104")
    _activo(db_session, tid)
    ss.sync_all(db_session)  # vincula el articulo (costo 48,50 USD: margen 34,6 %)
    db_session.refresh(p)
    assert not p.margin_hold
    assert _settings(db_session, tid)["margin_guard_last"]["reason"] == "catálogo de Eurocomp"
    eurocomp["items"] = [dict(CAMARA, precio="65.00")]  # +34 %: margen 12,4 %
    ss.refresh_linked(db_session)
    db_session.refresh(p)
    assert p.cost == Decimal(65) and p.margin_hold and p.margin_info["dudoso"] is True
    assert p.price == Decimal(38000)
    assert _settings(db_session, tid)["margin_guard_last"]["reason"] == "costos de Eurocomp"


def test_lista_excel_de_proveedor_corre_el_guardian(client, auth, db_session):
    """Sin credenciales de Eurocomp la lista llega en Excel: sus costos tambien pasan por el guardian en el acto (antes
    quedaba a la venta bajo el minimo hasta la red del dia siguiente)."""
    from tests.test_proveedores import LISTA, _by_code, _imp, _tax_ids

    tid = auth["tenant"]["id"]
    _shop(client)
    _activo(db_session, tid)
    pr = client.post(
        "/products",
        json={"name": "Cámara bullet 4 MP", "code": "CAM-B4", "price": 52000, "supplier_sku": "SG2104", "show_on_web": True, "tax_ids": _tax_ids(client)},
    ).json()
    assert _imp(client, commit=True).status_code == 200  # costo 70,91 USD: margen 30,1 %
    p = db_session.get(Product, pr["id"])
    db_session.refresh(p)
    assert Decimal(str(p.cost)) == Decimal("70.91") and not p.margin_hold
    cara = [list(r) for r in LISTA]
    for r in cara:
        if len(r) > 1 and r[1] == "SG2104":
            r[4] = 95  # 95 x 512,35 = 48 673: margen 6,4 %
    res = _imp(client, rows=cara, commit=True)
    assert res.status_code == 200 and res.json()["margin_guard"]["nuevos_retenidos"] == 1
    db_session.refresh(p)
    assert p.margin_hold and p.price == Decimal(52000)
    assert _settings(db_session, tid)["margin_guard_last"]["reason"] == "lista de proveedor"

    # sin ninguna tasa, el detalle de un articulo no vinculado abre igual (la propuesta queda sin precio)
    item = _by_code(client, "SG2010")
    db_session.execute(delete(ExchangeRate))
    db_session.commit()
    r = client.get(f"/supplier-catalog/items/{item['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["proposal"]["price"] is None and r.json()["proposal"]["fx"] is None


def test_solo_quien_ve_costos_revisa_y_aplica(client, auth, db_session):
    tid = auth["tenant"]["id"]
    _shop(client)
    p = _producto(client, db_session, tid, code="PERM-1", cost="140")
    _activo(db_session, tid)
    margin_guard.run_all(db_session)
    for rol in ("ventas", "supervisor", "inventario"):
        h = _login_role(client, db_session, tid, f"{rol}-margen@ejemplo.com", rol)
        assert client.post(f"/approvals/prices/{p.id}/apply", json={"price": 110400}, headers=h).status_code == 403, rol
        assert client.post("/approvals/prices/run", headers=h).status_code == 403, rol
    db_session.refresh(p)
    assert p.margin_hold and p.price == Decimal(78900)
    r = client.post("/approvals/prices/run")
    assert r.status_code == 200 and r.json()["retenidos"] >= 1


def test_resumen_diario_al_admin_una_vez_por_dia_y_sin_bcc(client, auth, db_session):
    """El resumen trae margenes y precios (el costo se deduce): nunca va en copia al BCC de las facturas."""
    tid = auth["tenant"]["id"]
    _shop(client)
    assert tareas.daily_summary(db_session) == 0  # nada bajo el minimo: no hay correo
    p = _producto(client, db_session, tid, code="RES-1", cost="140")
    _activo(db_session, tid, bcc=["contador@ejemplo.com"])
    margin_guard.run_all(db_session)
    antes = db_session.scalar(select(EmailOutbox.id).order_by(EmailOutbox.id.desc())) or 0
    assert tareas.daily_summary(db_session) == 1
    assert tareas.daily_summary(db_session) == 0  # el beat dispara dos veces: un solo correo
    correos = db_session.scalars(select(EmailOutbox).where(EmailOutbox.id > antes)).all()
    assert correos and all(c.subject.startswith("Margen: 1 producto(s) bajo el 25") for c in correos)
    assert all(c.bcc is None and c.entity == "margin_summary" for c in correos)
    assert p.name in correos[0].html and "Precios por revisar" in correos[0].html and "nunca cambia un precio solo" in correos[0].html
    assert "/aprobaciones?seccion=precios_por_revisar" in correos[0].html


def test_editar_a_mano_un_precio_retenido_lo_libera_ya(client, auth, db_session):
    """Alguien corrige el precio en Productos en lugar de "Aplicar": si ya cumple el minimo vuelve a la venta en el
    acto (sin esperar al proximo refresco); si sigue bajo, sigue fuera con el sugerido al dia. Editar nunca saca de
    venta por si solo y nunca reprecia."""
    tid = auth["tenant"]["id"]
    _shop(client)
    p = _producto(client, db_session, tid, code="EDIT-1", cost="140")
    _activo(db_session, tid)
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


def test_sin_costo_o_sin_precio_sale_de_la_revision(client, auth, db_session):
    """Un retenido que se queda sin costo (o sin precio) no queda "Consultar" para siempre con un "Aplicar" que falla."""
    tid = auth["tenant"]["id"]
    _shop(client)
    p = _producto(client, db_session, tid, code="SINC-1", cost="140")
    q = _producto(client, db_session, tid, code="SINP-1", cost="140")
    _activo(db_session, tid)
    margin_guard.run_all(db_session)
    db_session.refresh(p)
    db_session.refresh(q)
    assert p.margin_hold and q.margin_hold
    _set_cost(db_session, p, None)
    q.price = Decimal(0)
    db_session.commit()
    out = margin_guard.run_all(db_session)[tid]
    db_session.refresh(p)
    db_session.refresh(q)
    assert out["liberados"] == 2 and not p.margin_hold and p.margin_flagged_at is None and not q.margin_hold
    assert _pub(client, q.id)["price_on_request"] is True  # sin precio la tienda ya dice "Consultar"
    ids = [i["id"] for i in _sec(client, "precios_por_revisar")["items"]]
    assert p.id not in ids and q.id not in ids


def test_variante_mas_barata_bajo_el_minimo(client, auth, db_session):
    """La tienda cobra la variante a su precio: una variante mas barata que el base, con el mismo costo, saca de venta
    el producto y "Aplicar" sube solo lo que esta bajo el minimo (el base, sano, no se toca)."""
    tid = auth["tenant"]["id"]
    _shop(client)
    p = _producto(client, db_session, tid, code="VAR-1")
    v = ProductVariant(tenant_id=tid, product_id=p.id, name="Blanco", code="VAR-1-B", price=Decimal(60000), options={"Color": "Blanco"}, active=True)
    db_session.add(v)
    db_session.commit()
    _activo(db_session, tid)
    out = margin_guard.run_all(db_session)[tid]
    db_session.refresh(p)
    assert out["nuevos_retenidos"] == 1 and p.margin_hold and p.margin_info["margin_pct"] == "14.6"  # (60000 - 51235) / 60000
    it = _item(client, p.id)
    assert "1 variante(s)" in it["subtitle"] and "1 variante(s)" in it["action"]["confirm"]
    assert Decimal(str(it["action"]["body"]["price"])) == Decimal(78900)
    assert _aplicar(client, it).status_code == 200
    db_session.refresh(p)
    db_session.refresh(v)
    assert p.price == Decimal(78900) and v.price == Decimal(78900) and not p.margin_hold


def test_salto_dudoso_sobre_el_minimo_se_avisa_sin_sacar_de_venta(client, auth, db_session):
    """Un salto de costo de 25 % o mas se marca "dudoso" aunque el margen siga sano (subida en un producto de margen
    alto, o una caida por una lista por caja contra por unidad): no sale de venta, va en el resumen y la referencia no
    se mueve hasta que el costo se estabilice."""
    tid = auth["tenant"]["id"]
    _shop(client)
    alto = _producto(client, db_session, tid, code="DUD-ALTO", price="150000")
    cae = _producto(client, db_session, tid, code="DUD-CAE")
    _activo(db_session, tid)
    margin_guard.run_all(db_session)
    _set_cost(db_session, alto, 130)  # +30 %, margen 55,6 %
    _set_cost(db_session, cae, 50)  # -50 %
    out = margin_guard.run_all(db_session)[tid]
    assert out["dudosos"] == 2 and out["bajo_minimo"] == 0 and out["nuevos_retenidos"] == 0
    for p in (alto, cae):
        db_session.refresh(p)
        assert p.margin_info["dudoso"] is True and not p.margin_hold and p.margin_flagged_at is None
        assert p.margin_info["cost_ref"] == "100"  # la referencia no se mueve
    assert cae.margin_info["jump_pct"] == "-50"
    antes = db_session.scalar(select(EmailOutbox.id).order_by(EmailOutbox.id.desc())) or 0
    assert tareas.daily_summary(db_session) == 1
    c = db_session.scalars(select(EmailOutbox).where(EmailOutbox.id > antes)).first()
    assert "DUD-ALTO" in c.html and "DUD-CAE" in c.html and "dudoso" in c.html
    # estable DUDOSO_DIAS dias: el costo nuevo queda como referencia y deja de avisarse
    viejo = (datetime.now(UTC) - timedelta(days=margin_guard.DUDOSO_DIAS + 1)).isoformat()
    alto.margin_info = {**alto.margin_info, "jump_since": viejo}
    db_session.commit()
    margin_guard.run_all(db_session)
    db_session.refresh(alto)
    assert alto.margin_info["dudoso"] is False and alto.margin_info["cost_ref"] == "130"


def test_el_guardian_no_revierte_ajustes_guardados_mientras_corria(client, auth, db_session):
    """El guardian escribe su resumen releyendo la empresa y solo con sus llaves: un PUT /settings confirmado mientras
    corria (aqui, escrito por fuera de la sesion) no se pierde."""
    tid = auth["tenant"]["id"]
    _shop(client)
    _producto(client, db_session, tid, code="RACE-1")
    t = db_session.get(Tenant, tid)
    nuevo = {**(t.settings or {}), "bcc": ["auditor@ejemplo.com"], "cost_roles": ["admin"]}
    db_session.execute(update(Tenant).where(Tenant.id == tid).values(settings=nuevo).execution_options(synchronize_session=False))
    assert (t.settings or {}).get("bcc") != ["auditor@ejemplo.com"]  # la copia en memoria quedo vieja
    margin_guard.run_safe(db_session, t, reason="carrera")
    db_session.expire_all()
    st = _settings(db_session, tid)
    assert st["bcc"] == ["auditor@ejemplo.com"] and st["cost_roles"] == ["admin"] and st["margin_guard_last"]["reason"] == "carrera"


def test_tasa_desde_el_portal_solo_revisa_la_empresa_que_la_cargo(client, auth, db_session):
    """La tasa es global, pero el request de una empresa no saca de venta ni escribe en otra (la revisa la red diaria
    del worker). Ademas, cargar tasas tiene limite de frecuencia."""
    tid = auth["tenant"]["id"]
    _shop(client)
    otra = Tenant(slug="otra-margen", name="Otra empresa de prueba", default_currency="CRC", settings={"margin_guard_mode": "activo"})
    db_session.add(otra)
    db_session.flush()
    ajeno = Product(
        tenant_id=otra.id,
        code="AJ-1",
        name="Equipo ajeno",
        price=Decimal(78900),
        currency="CRC",
        cost=Decimal(140),
        cost_currency="USD",
        show_on_web=True,
        active=True,
    )
    db_session.add(ajeno)
    db_session.commit()
    propio = _producto(client, db_session, tid, code="PROPIO-1", cost="140")
    _activo(db_session, tid)
    assert client.put("/fx", json={"date": HOY.isoformat(), "currency": "USD", "sell": 512.35, "buy": 505.10}).status_code == 200
    db_session.refresh(propio)
    db_session.refresh(ajeno)
    db_session.refresh(otra)
    assert propio.margin_hold and not ajeno.margin_hold and "margin_guard_last" not in (otra.settings or {})
    tareas.guard_all(db_session)  # la red diaria si la revisa
    db_session.refresh(ajeno)
    assert ajeno.margin_hold
    for _ in range(9):
        client.put("/fx", json={"date": HOY.isoformat(), "currency": "USD", "sell": 512.35, "buy": 505.10})
    r = client.put("/fx", json={"date": HOY.isoformat(), "currency": "USD", "sell": 512.35, "buy": 505.10})
    assert r.status_code == 429


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
    _activo(db_session, tid)
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
