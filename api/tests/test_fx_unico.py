"""Tipo de cambio unico (plan 1.12): services/fx.fx() es la unica fuente y nunca devuelve 1 para una moneda distinta
de CRC. Sin ninguna tasa lanza FxMissing: los documentos responden un 422 claro y la tienda pasa a "Consultar" solo
lo que necesita convertirse (lo que esta en colones se sigue vendiendo, nunca un 500). La tasa vieja (mas de 2 dias
habiles) se marca, contando con el dia de Costa Rica y no el de UTC."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.models import ExchangeRate, Expense, Product, ProductTax, Tax
from app.services import fx as fxsvc
from app.services import pricing
from app.services.documents import today_fx
from tests.test_inventario_reservas import _entrada, _wh
from tests.test_tienda_checkout import SLUG, _body, _shop


def _sin_tasas(db):
    db.execute(delete(ExchangeRate))
    db.commit()


def _tasa(db, dia: date, sell="512.35", buy="505.10", source="manual"):
    db.add(ExchangeRate(date=dia, currency="USD", sell=Decimal(sell), buy=Decimal(buy), source=source))
    db.commit()


def _producto(db, tid, code, price, currency="CRC", **kw) -> Product:
    iva = db.scalar(select(Tax).where(Tax.tenant_id == tid, Tax.rate_code == "08"))
    p = Product(tenant_id=tid, code=code, name=kw.pop("name", f"Equipo {code}"), price=Decimal(str(price)), currency=currency, show_on_web=True, active=True, **kw)
    p.taxes.append(ProductTax(tax_id=iva.id))
    db.add(p)
    db.commit()
    return p


# ---------- la funcion unica ----------
def test_crc_es_base_y_sin_tasa_nunca_es_uno(client, auth, db_session):
    assert fxsvc.fx(db_session, "CRC") == fxsvc.Tasa(Decimal(1), None, "base", False)
    assert fxsvc.fx(db_session, "USD").tasa == Decimal("512.35")  # la semilla trae la de hoy
    _sin_tasas(db_session)
    with pytest.raises(fxsvc.FxMissing) as e:
        fxsvc.fx(db_session, "USD")
    assert "tipo de cambio de USD" in str(e.value)
    # las copias viejas delegan: ninguna cae a 1 en silencio
    with pytest.raises(fxsvc.FxMissing):
        today_fx(db_session, "USD")
    with pytest.raises(fxsvc.FxMissing):
        pricing.convert(db_session, 10, "USD", "CRC")
    assert today_fx(db_session, "CRC") == (Decimal(1), Decimal(1))
    assert pricing.convert(db_session, 10, "CRC", "CRC") == Decimal(10)
    assert fxsvc.rate_or_none(db_session, "USD") is None
    # una tasa en 0 tampoco sirve
    _tasa(db_session, date(2026, 10, 1), sell="0", buy="0")
    with pytest.raises(fxsvc.FxMissing):
        fxsvc.fx(db_session, "USD")


def test_tasa_vieja_por_dias_habiles_y_fecha_pedida(client, auth, db_session):
    _sin_tasas(db_session)
    _tasa(db_session, date(2026, 10, 14), sell="510", source="bccr")  # miercoles
    viernes, lunes = date(2026, 10, 16), date(2026, 10, 19)
    t = fxsvc.fx(db_session, "USD", viernes)
    assert t.tasa == Decimal(510) and t.fecha == date(2026, 10, 14) and t.fuente == "bccr" and not t.vieja  # jue y vie: 2
    assert fxsvc.fx(db_session, "USD", lunes).vieja  # jue, vie y lun: 3 dias habiles
    assert fxsvc.business_days_between(date(2026, 10, 16), date(2026, 10, 19)) == 1  # el fin de semana no cuenta
    # fecha anterior a toda tasa: la mas cercana posterior, marcada vieja (nunca 1)
    t = fxsvc.fx(db_session, "USD", date(2026, 1, 5))
    assert t.tasa == Decimal(510) and t.vieja
    # la de esa fecha o la ultima anterior
    _tasa(db_session, date(2026, 10, 19), sell="515")
    assert fxsvc.fx(db_session, "USD", date(2026, 10, 18)).tasa == Decimal(510)
    assert fxsvc.fx(db_session, "USD", lunes).tasa == Decimal(515)
    assert fxsvc.fx(db_session, "USD", side="buy").tasa == Decimal("505.10")


def test_vieja_se_cuenta_con_el_dia_de_costa_rica(client, auth, db_session, monkeypatch):
    """Lunes 19/10 a las 21:00 en Costa Rica ya es martes 20/10 en UTC: la tasa del jueves 15 tiene 2 dias habiles
    (viernes y lunes), no 3. El borde de las 18:00-24:00 CR no la marca vieja antes de tiempo."""
    _sin_tasas(db_session)
    _tasa(db_session, date(2026, 10, 15))

    class Reloj(datetime):
        @classmethod
        def now(cls, tz=None):
            t = datetime(2026, 10, 20, 3, 0, tzinfo=UTC)
            return t.astimezone(tz) if tz else t.replace(tzinfo=None)

    monkeypatch.setattr(fxsvc, "datetime", Reloj)
    assert fxsvc.cr_today() == date(2026, 10, 19)
    assert not fxsvc.fx(db_session, "USD").vieja
    info = fxsvc.info(db_session, "USD")
    assert info["business_days_old"] == 2 and info["stale"] is False


def test_fx_today_dice_fecha_fuente_y_si_esta_vieja(client, auth, db_session):
    r = client.get("/fx/today")
    assert r.status_code == 200
    body = r.json()
    assert Decimal(body["sell"]) == Decimal("512.35") and body["source"] == "manual" and body["source_label"] == "Manual"
    assert body["stale"] is False and body["date"]
    _sin_tasas(db_session)
    _tasa(db_session, date(2026, 1, 2), source="bccr")
    body = client.get("/fx/today").json()
    assert body["stale"] is True and body["source_label"] == "BCCR" and body["date"] == "2026-01-02"
    _sin_tasas(db_session)
    assert client.get("/fx/today").json() is None


def test_tasa_de_documento_guardado(client, auth, db_session):
    """Factura, gasto o comprobante guardado: su tasa si es valida; si falta o quedo en 1 (el sistema viejo), la del
    BCCR de su fecha; sin ninguna, FxMissing."""
    _sin_tasas(db_session)
    _tasa(db_session, date(2026, 9, 1), sell="500")
    _tasa(db_session, date(2026, 10, 1), sell="520")
    assert fxsvc.doc_rate(db_session, "CRC", None, None) == 1
    assert fxsvc.doc_rate(db_session, "USD", Decimal("505.5"), date(2026, 9, 15)) == Decimal("505.5")
    assert fxsvc.doc_rate(db_session, "USD", 1, date(2026, 9, 15)) == Decimal(500)  # el 1 viejo no se cree
    assert fxsvc.doc_rate(db_session, "USD", None, date(2026, 10, 2)) == Decimal(520)
    _sin_tasas(db_session)
    with pytest.raises(fxsvc.FxMissing):
        fxsvc.doc_rate(db_session, "USD", 1, date(2026, 9, 15))


def test_gasto_en_dolares_con_tasa_uno_no_suma_dolares_como_colones(client, auth, db_session):
    tid = auth["tenant"]["id"]
    _tasa(db_session, date(2026, 9, 10), sell="500")
    e = Expense(tenant_id=tid, date=date(2026, 9, 10), description="Licencia anual", currency="USD", fx_rate=Decimal(1), subtotal=Decimal(100), tax_amount=Decimal(0), total=Decimal(100))
    db_session.add(e)
    db_session.commit()
    rows = client.get("/expenses").json()
    row = next(x for x in (rows["items"] if isinstance(rows, dict) else rows) if x["id"] == e.id)
    assert Decimal(str(row["total_crc"])) == Decimal("50000.00")  # 100 x 500 del BCCR de su fecha, no 100 x 1


# ---------- documentos: 422 claro ----------
def test_documento_en_dolares_sin_tasa_da_422_claro(client, auth, db_session):
    cust = client.get("/customers").json()["items"][0]
    linea = {"name": "Servicio", "quantity": 1, "unit_price": 100, "tax_rate": 13}
    _sin_tasas(db_session)
    r = client.post("/invoices", json={"customer_id": cust["id"], "currency": "USD", "lines": [linea]})
    assert r.status_code == 422 and "tipo de cambio" in r.json()["detail"]
    r = client.post("/quotes", json={"customer_id": cust["id"], "currency": "USD", "lines": [linea]})
    assert r.status_code == 422 and "tipo de cambio" in r.json()["detail"]
    # en colones nada cambia
    r = client.post("/invoices", json={"customer_id": cust["id"], "currency": "CRC", "lines": [linea]})
    assert r.status_code in (200, 201), r.text
    assert Decimal(str(r.json()["fx_sell"])) == 1


# ---------- tienda: degrada solo lo afectado ----------
def test_tienda_sin_tasa_pasa_a_consultar_solo_lo_que_esta_en_dolares(client, auth, db_session):
    tid = auth["tenant"]["id"]
    prods = _shop(client)
    crc = next(p for p in prods if p["currency"] == "CRC")
    usd = _producto(db_session, tid, "USD-1", "100.00", currency="USD", name="Cámara en dólares")
    _entrada(client, usd.id, _wh(client)["id"], 5)
    lista = {p["id"]: p for p in client.get(f"{SLUG}/products").json()}
    assert Decimal(str(lista[usd.id]["display_price"])) == Decimal("57895.55")  # 100 x 512.35 + IVA

    _sin_tasas(db_session)
    r = client.get(f"{SLUG}/products")
    assert r.status_code == 200
    lista = {p["id"]: p for p in r.json()}
    assert lista[usd.id]["price_on_request"] is True and Decimal(str(lista[usd.id]["display_price"])) == 0
    assert lista[crc["id"]]["price_on_request"] is False and Decimal(str(lista[crc["id"]]["display_price"])) > 0
    # la vitrina, la ficha y la portada siguen arriba (nunca un 500)
    for path in ("/site", "/showcase", "/catalog", f"/item/x-{usd.id}", f"/item/x-{crc['id']}"):
        assert client.get(f"{SLUG}{path}").status_code == 200, path
    card = client.get(f"{SLUG}/item/x-{usd.id}").json()
    assert card["price_on_request"] is True
    assert client.get(f"{SLUG}/checkout-config").status_code == 200
    # el checkout de lo que esta en colones sigue funcionando
    r = client.post(f"{SLUG}/checkout", json=_body(crc["id"], lines=[{"product_id": crc["id"], "quantity": 1}]))
    assert r.status_code == 201, r.text
    # lo que esta en dolares no se vende a un precio inventado
    r = client.post(f"{SLUG}/checkout", json=_body(usd.id, lines=[{"product_id": usd.id, "quantity": 1}]))
    assert r.status_code == 409 and "precio a consultar" in r.json()["detail"]
    assert client.post(f"{SLUG}/quote", json=_body(usd.id, lines=[{"product_id": usd.id, "quantity": 1}])).status_code == 409


def test_paneles_internos_sin_tasa_no_se_caen(client, auth, db_session):
    """La lista de productos (con costos), la caja y el embudo siguen funcionando sin tasa: solo lo que hay que
    convertir queda sin dato."""
    tid = auth["tenant"]["id"]
    usd = _producto(db_session, tid, "USD-2", "100.00", currency="USD", cost=Decimal(40), cost_currency="USD")
    _sin_tasas(db_session)
    assert client.get("/products").status_code == 200
    cat = client.get("/pos/catalog")
    assert cat.status_code == 200
    item = next(i for i in cat.json() if i["id"] == usd.id)
    assert item["pos_price"] is None  # sin tasa no se convierte con 1
    assert any(i["pos_price"] is not None for i in cat.json() if i["currency"] == "CRC")
    assert client.get("/opportunities/board").status_code == 200


def test_tienda_en_dolares_sin_tasa_no_tumba_portada_ni_checkout(client, auth, db_session):
    """Tienda que vende en dolares: sin tasa, lo que esta en colones (y las tarifas de envio, que se configuran en
    colones) queda "a consultar"; la portada y el checkout-config responden igual (nunca un 422 ni un 500)."""
    prods = _shop(client, free_shipping_from=50000)
    assert client.put("/store", json={"currency": "USD"}).status_code == 200
    crc = next(p for p in prods if p["currency"] == "CRC")
    home = client.get(SLUG)
    assert home.status_code == 200 and all(r["display_amount"] is not None for r in home.json()["shipping_rates"])
    _sin_tasas(db_session)
    home = client.get(SLUG)
    assert home.status_code == 200 and all(r["display_amount"] is None for r in home.json()["shipping_rates"])
    cfg = client.get(f"{SLUG}/checkout-config")
    assert cfg.status_code == 200 and cfg.json()["free_shipping_from"] is None
    assert all(r["display_amount"] is None for r in cfg.json()["shipping_rates"])
    lista = {p["id"]: p for p in client.get(f"{SLUG}/products").json()}
    assert lista[crc["id"]]["price_on_request"] is True
    r = client.post(f"{SLUG}/checkout", json=_body(crc["id"], lines=[{"product_id": crc["id"], "quantity": 1}]))
    assert r.status_code == 409 and "precio a consultar" in r.json()["detail"]


def test_gasto_sin_tasa_de_su_fecha_usa_la_mas_cercana_y_sin_ninguna_da_422(client, auth, db_session):
    """Registrar un gasto en dolares: la tasa del BCCR de su fecha; si no hay ninguna anterior, la mas cercana con
    aviso (antes se guardaba 1 "con aviso"); sin ninguna tasa cargada, 422 claro."""
    _sin_tasas(db_session)
    _tasa(db_session, date(2026, 9, 1), sell="500")
    assert fxsvc.for_date(db_session, "USD", date(2026, 9, 15)) == (Decimal(500), None)
    tasa, aviso = fxsvc.for_date(db_session, "USD", date(2026, 8, 1))
    assert tasa == Decimal(500) and "01/09/2026" in aviso
    assert fxsvc.for_date(db_session, "CRC", date(2026, 8, 1)) == (Decimal(1), None)
    base = {"description": "Licencia", "currency": "USD", "subtotal": 100, "tax_rate": 0, "iva_credit": "no_credito"}
    r = client.post("/expenses", json={**base, "date": "2026-08-01"})
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert Decimal(str(body["fx_rate"])) == Decimal(500)
    assert any("se usó el del 01/09/2026" in w for w in body["warnings"])
    _sin_tasas(db_session)
    r = client.post("/expenses", json={**base, "date": "2026-08-02"})
    assert r.status_code == 422 and "tipo de cambio" in r.json()["detail"]
