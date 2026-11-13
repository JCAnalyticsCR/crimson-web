"""Estado de resultados mensual (Contabilidad -> Contable -> Resultados, reunion con la contadora 10/11/2026).
Repo publico: solo datos ficticios."""

from datetime import UTC, date, datetime
from decimal import Decimal
from io import BytesIO

from openpyxl import load_workbook

from app.models import EInvoiceDocument, Invoice, Product, Project, StockMovement, Warehouse
from tests.aliados_helpers import interno

D = Decimal
URL = "/accounting/income-statement"
SEP, OCT, NOV = date(2026, 9, 15), date(2026, 10, 10), date(2026, 11, 5)


def _cust(client, headers=None):
    return client.get("/customers", headers=headers).json()["items"][0]


def _prod(db_session, tid, code, kind):
    p = Product(tenant_id=tid, code=code, name=f"Artículo {code}", item_type=kind, price=0, currency="CRC")
    db_session.add(p)
    db_session.commit()
    return p.id


def _inv(client, lines, on, headers=None, **extra):
    body = {"customer_id": _cust(client, headers)["id"], "issue_date": on.isoformat(), "lines": lines, **extra}
    r = client.post("/invoices", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _cat(client, name, headers=None, **extra):
    r = client.post("/expense-categories", json={"name": name, **extra}, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _exp(client, cat, monto, on, headers=None, **extra):
    body = {"description": "Gasto de prueba", "date": on.isoformat(), "subtotal": monto, "tax_rate": 13, "category_id": cat, **extra}
    r = client.post("/expenses", json=body, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _get(client, headers=None, **params):
    r = client.get(URL, params={"from": "2026-09", "to": "2026-11", **params}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _row(x, key):
    return next(r for r in x["rows"] if r["key"] == key)


def _row_label(x, label):
    return next(r for r in x["rows"] if r["label"] == label)


def _vals(r):
    return [D(v) for v in r["values"]], D(r["total"])


def test_ingresos_y_gastos_por_categoria_por_mes(client, auth, db_session):
    tid = auth["tenant"]["id"]
    eq, sv = _prod(db_session, tid, "ER-EQ", "producto"), _prod(db_session, tid, "ER-SV", "servicio")
    # septiembre: equipo 100 000 + servicio 50 000 + linea sin catalogo 10 000 (servicios, como en QuickBooks)
    _inv(
        client,
        [
            {"product_id": eq, "name": "Cámara", "quantity": 2, "unit_price": 50000, "tax_rate": 13},
            {"product_id": sv, "name": "Configuración", "quantity": 1, "unit_price": 50000, "tax_rate": 13},
            {"name": "Viático", "quantity": 1, "unit_price": 10000, "tax_rate": 13},
        ],
        SEP,
    )
    # octubre: factura de proyecto (todo a Proyectos) y una con descuento global del 10 %
    pinv = _inv(client, [{"product_id": eq, "name": "Kit", "quantity": 1, "unit_price": 300000, "tax_rate": 13}], OCT)
    db_session.add(Project(tenant_id=tid, number="PRY-ER", name="Proyecto ficticio", invoice_id=pinv["id"]))
    db_session.commit()
    _inv(client, [{"product_id": eq, "name": "Sensor", "quantity": 1, "unit_price": 200000, "tax_rate": 13}], OCT, discount_type="percent", discount_value=10)
    # gastos: compras (costo de ventas por el nombre), alquiler y combustible (operacion), uno anulado (no cuenta)
    compras = _cat(client, "Compras de equipo")
    alq = _cat(client, "Alquiler")
    comb = _cat(client, "Combustible")
    _exp(client, compras, 80000, SEP)
    _exp(client, alq, 40000, OCT)
    _exp(client, comb, 15000, NOV, iva_credit="no_credito")  # condicion 04: el IVA es gasto
    _exp(client, alq, 999999, NOV, status="anulado")

    x = _get(client)
    assert x["months"] == ["2026-09", "2026-10", "2026-11"] and x["currency"] == "CRC" and x["basis"] == "devengado"
    assert "devengada" in x["note"]
    assert _vals(_row(x, "equipos")) == ([D("100000.00"), D("200000.00"), D(0)], D("300000.00"))
    assert _vals(_row(x, "servicios")) == ([D("60000.00"), D(0), D(0)], D("60000.00"))
    assert _vals(_row(x, "proyectos")) == ([D(0), D("300000.00"), D(0)], D("300000.00"))
    assert _vals(_row(x, "descuentos")) == ([D(0), D("-20000.00"), D(0)], D("-20000.00"))
    assert _vals(_row(x, "total_ingresos")) == ([D("160000.00"), D("480000.00"), D(0)], D("640000.00"))
    assert _vals(_row_label(x, "Compras de equipo")) == ([D("80000.00"), D(0), D(0)], D("80000.00"))
    assert _vals(_row(x, "total_costo_ventas"))[1] == D("80000.00")
    assert _vals(_row(x, "utilidad_bruta")) == ([D("80000.00"), D("480000.00"), D(0)], D("560000.00"))
    assert _vals(_row_label(x, "Combustible")) == ([D(0), D(0), D("16950.00")], D("16950.00"))
    assert _vals(_row_label(x, "Alquiler")) == ([D(0), D("40000.00"), D(0)], D("40000.00"))  # el anulado no cuenta
    assert _vals(_row(x, "total_gastos_operacion"))[1] == D("56950.00")
    assert _vals(_row(x, "utilidad_neta")) == ([D("80000.00"), D("440000.00"), D("-16950.00")], D("503050.00"))
    assert x["summary"]["utilidad_neta"] == "503050.00"
    assert x["margins"]["bruto_pct"][0] == "50.0" and x["margins"]["bruto_pct"][2] is None
    # orden de las secciones: ingresos, costo de ventas, utilidad bruta, gastos de operacion, utilidad neta
    keys = [r["key"] for r in x["rows"]]
    assert (
        keys.index("sec_ingresos")
        < keys.index("total_ingresos")
        < keys.index("utilidad_bruta")
        < keys.index("total_gastos_operacion")
        < keys.index("utilidad_neta")
    )
    assert "notas_credito" not in keys  # sin NC en el periodo no hay linea

    # Excel: un mes por columna + total, numeros como numeros
    xl = client.get(URL, params={"from": "2026-09", "to": "2026-11", "format": "xlsx"})
    assert xl.status_code == 200, xl.text
    ws = load_workbook(BytesIO(xl.content))["Estado de resultados"]
    assert [ws.cell(6, c).value for c in range(1, 6)] == ["Concepto", "set 2026", "oct 2026", "nov 2026", "Total"]
    textos = [ws.cell(i, 1).value for i in range(1, ws.max_row + 1)]
    fila = textos.index("Utilidad neta (antes del impuesto sobre la renta)") + 1
    assert ws.cell(fila, 5).value == 503050 and "₡" in ws.cell(fila, 5).number_format


def test_usd_con_el_tipo_de_cambio_de_cada_documento(client, auth, db_session):
    tid = auth["tenant"]["id"]
    sv = _prod(db_session, tid, "ER-USD", "servicio")
    _inv(client, [{"product_id": sv, "name": "Soporte", "quantity": 1, "unit_price": 100, "tax_rate": 13}], OCT, currency="USD", fx_sell=520)
    _inv(client, [{"product_id": sv, "name": "Soporte", "quantity": 1, "unit_price": 100, "tax_rate": 13}], NOV, currency="USD", fx_sell=505)
    lic = _cat(client, "Suscripciones")
    _exp(client, lic, 20, OCT, currency="USD", fx_rate=510)  # con credito: 20 USD x 510 = 10 200
    x = _get(client)
    assert _vals(_row(x, "servicios")) == ([D(0), D("52000.00"), D("50500.00")], D("102500.00"))
    assert _vals(_row_label(x, "Suscripciones")) == ([D(0), D("10200.00"), D(0)], D("10200.00"))


def test_nota_de_credito_resta_en_su_mes_y_anuladas_rechazadas_no_cuentan(client, auth, db_session):
    tid = auth["tenant"]["id"]
    _inv(client, [{"name": "Venta buena", "quantity": 1, "unit_price": 100000, "tax_rate": 13}], SEP)
    con_nc = _inv(client, [{"name": "Venta con NC", "quantity": 1, "unit_price": 50000, "tax_rate": 13}], SEP)
    anulada = _inv(client, [{"name": "Anulada sin NC", "quantity": 1, "unit_price": 70000, "tax_rate": 13}], SEP)
    rechazada = _inv(client, [{"name": "Rechazada", "quantity": 1, "unit_price": 30000, "tax_rate": 13}], OCT)
    db_session.get(Invoice, con_nc["id"]).status = "anulada"
    db_session.get(Invoice, anulada["id"]).status = "anulada"
    db_session.get(Invoice, rechazada["id"]).einvoice_status = "rechazada"
    db_session.add(
        EInvoiceDocument(
            tenant_id=tid,
            invoice_id=con_nc["id"],
            doc_type="NC",
            consecutive="00100001030000000077",
            clave="8" * 50,
            provider="hacienda",
            status="aceptada",
            mode="produccion",
            created_at=datetime(2026, 10, 20, 18, 0, tzinfo=UTC),
        )
    )
    db_session.commit()
    x = _get(client)
    # la anulada por NC sigue en septiembre; la NC resta en octubre; la anulada sin NC y la rechazada no cuentan
    assert _vals(_row(x, "servicios")) == ([D("150000.00"), D(0), D(0)], D("150000.00"))
    assert _vals(_row(x, "notas_credito")) == ([D(0), D("-50000.00"), D(0)], D("-50000.00"))
    assert _vals(_row(x, "total_ingresos")) == ([D("150000.00"), D("-50000.00"), D(0)], D("100000.00"))
    assert x["counts"]["facturas"] == 2


def test_planilla_y_comisiones_salen_como_sus_categorias(client, auth, db_session):
    planilla = _cat(client, "Planilla")
    comisiones = _cat(client, "Comisiones")
    _exp(client, planilla, 900000, OCT, tax_rate=0, iva_credit="no_credito")
    _exp(client, comisiones, 45000, OCT, tax_rate=0, iva_credit="no_credito")
    x = _get(client)
    oper = [r["label"] for r in x["rows"] if r["key"].startswith("cat_")]
    assert oper[:2] == ["Planilla", "Comisiones"]  # de mayor a menor
    assert _vals(_row(x, "total_gastos_operacion"))[1] == D("945000.00")


def test_aislamiento_entre_empresas(client, auth, db_session):
    from app.core.security import hash_password
    from app.models import Tenant, TenantUser, User

    _inv(client, [{"name": "Venta empresa A", "quantity": 1, "unit_price": 123000, "tax_rate": 13}], OCT)
    _exp(client, _cat(client, "Alquiler A"), 10000, OCT)
    t2 = Tenant(name="Empresa B ficticia", slug="empresa-b-resultados", settings={})
    db_session.add(t2)
    db_session.flush()
    pw = "Empresa-B-2026-res"
    u2 = User(email="admin.b.resultados@ejemplo.com", full_name="Admin B", password_hash=hash_password(pw))
    db_session.add(u2)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=t2.id, user_id=u2.id, role_code="admin"))
    db_session.commit()
    tok = client.post("/auth/login", json={"email": "admin.b.resultados@ejemplo.com", "password": pw}).json()["access_token"]
    hb = {"Authorization": f"Bearer {tok}"}
    xb = _get(client, headers=hb)
    assert xb["summary"]["ingresos"] == "0.00" and xb["summary"]["gastos_operacion"] == "0.00"
    assert not [r for r in xb["rows"] if r["key"].startswith("cat_")]
    xa = _get(client)
    assert xa["summary"]["ingresos"] == "123000.00"


def test_permisos_y_costo_de_inventario_solo_para_quien_ve_costos(client, auth, db_session):
    tid = auth["tenant"]["id"]
    eq = _prod(db_session, tid, "ER-KDX", "producto")
    inv = _inv(client, [{"product_id": eq, "name": "Cámara", "quantity": 2, "unit_price": 60000, "tax_rate": 13}], OCT)
    wh = db_session.query(Warehouse).filter_by(tenant_id=tid).first()
    if wh is None:
        wh = Warehouse(tenant_id=tid, name="Principal ficticia", is_default=True)
        db_session.add(wh)
        db_session.flush()
    db_session.add(
        StockMovement(
            tenant_id=tid,
            product_id=eq,
            warehouse_id=wh.id,
            kind="venta",
            quantity=D("-2"),
            unit_cost=D("35000"),
            invoice_id=inv["id"],
            at=datetime(2026, 10, 10, 15, 0, tzinfo=UTC),
        )
    )
    db_session.commit()
    # el admin ve costos: el costo del kardex sale como referencia (no se resta de la utilidad)
    x = _get(client)
    assert x["sees_costs"] is True
    assert _vals(x["inventory_cost"]) == ([D(0), D("70000.00"), D(0)], D("70000.00"))
    assert x["summary"]["utilidad_bruta"] == "120000.00"
    # contabilidad: ve el estado de resultados y lo exporta, pero no el costo de producto
    conta = interno(client, db_session, tid, "conta.resultados@ejemplo.com", "contabilidad")
    xc = _get(client, headers=conta)
    assert xc["sees_costs"] is False and xc["inventory_cost"] is None
    assert xc["summary"] == x["summary"]  # el mismo estado de resultados
    xl = client.get(URL, params={"from": "2026-10", "to": "2026-10", "format": "xlsx"}, headers=conta)
    assert xl.status_code == 200
    textos = [c.value for c in load_workbook(BytesIO(xl.content))["Estado de resultados"]["A"]]
    assert not any(isinstance(t, str) and "kardex" in t for t in textos)
    # ventas no tiene contabilidad
    ventas = interno(client, db_session, tid, "ventas.resultados@ejemplo.com", "ventas")
    assert client.get(URL, headers=ventas).status_code == 403
    # rango invalido o demasiado largo
    assert client.get(URL, params={"from": "2026-11", "to": "2026-09"}).status_code == 422
    assert client.get(URL, params={"from": "2024-01", "to": "2026-11"}).status_code == 422
    assert client.get(URL, params={"from": "2026-13"}).status_code == 422


def test_margen_sin_sentido_con_ventas_casi_nulas_sale_vacio(client, auth, db_session):
    """₡100 de venta contra ₡863 000 de gasto daba un margen de -863 253 %: fuera de ±1000 % se muestra «—»."""
    tid = auth["tenant"]["id"]
    sv = _prod(db_session, tid, "ER-MIN", "servicio")
    _inv(client, [{"product_id": sv, "name": "Prueba", "quantity": 1, "unit_price": 100, "tax_rate": 13}], NOV)
    _exp(client, _cat(client, "Alquiler"), 863000, NOV)
    x = _get(client)
    assert x["margins"]["neto_pct"][2] is None and x["margins"]["neto_pct"][3] is None
