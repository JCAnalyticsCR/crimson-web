"""IVA por tarifa en gastos y categoria automatica por proveedor (reunion con la contadora, 10/11/2026).

XML ficticio de un supermercado con 13 %, 4 %, 2 %, 1 %, una linea exenta y una exonerada; gasto manual; aceptacion
parcial y prorrata; relleno idempotente; asiento balanceado por tarifa; categoria aprendida y aislamiento entre
empresas. Todos los datos son ficticios (repo publico)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select

from app.models import AccountingRoleMapping, Expense, ExpenseCategory, ExpenseTaxLine, QboAccount, Supplier, SupplierCategoryRule, Tenant
from app.services import expense_tax
from app.services import journal as js
from app.tools import desglose_iva_gastos
from tests.hacienda_utils import EMPRESA, NS44, clave

HOY = date.today()
FECHA_HOY = f"{HOY.isoformat()}T10:00:00-06:00"
DDMMAA = HOY.strftime("%d%m%y")
D = Decimal

# (detalle, subtotal, codigo tarifa, tarifa, monto IVA, monto exonerado) -- None = linea sin impuesto (exenta)
LINEAS = [
    ("Leche ficticia", 10000, "02", "1.00", 100, 0),
    ("Medicamento ficticio", 5000, "03", "2.00", 100, 0),
    ("Shampoo ficticio", 30000, "08", "13.00", 3900, 0),
    ("Servicio ficticio", 10000, "04", "4.00", 400, 0),
    ("Libro ficticio", 8000, None, None, 0, 0),
    ("Equipo exonerado ficticio", 20000, "08", "13.00", 2600, 2600),
    ("Jabon ficticio", 10000, "08", "13.00", 1300, 0),
]
BASE, IVA = D(93000), D(5800)
TOTAL = BASE + IVA


def _cl(n: int) -> str:
    return clave(fecha=DDMMAA, emisor="003101777777", consecutivo=f"0010000101{n:010d}")


def xml_super(n: int, emisor: str = "3101777777", nombre: str = "Supermercado Ficticio SA") -> str:
    ns = NS44 + "facturaElectronica"
    lineas = ""
    for i, (det, sub, code, tarifa, monto, exo) in enumerate(LINEAS, 1):
        imp = ""
        if code is not None:
            ex = f"<Exoneracion><TipoDocumentoEX1>03</TipoDocumentoEX1><NumeroDocumento>EXO-FICTICIA-1</NumeroDocumento><TarifaExonerada>13.00</TarifaExonerada><MontoExoneracion>{exo:.5f}</MontoExoneracion></Exoneracion>" if exo else ""
            imp = f"<Impuesto><Codigo>01</Codigo><CodigoTarifaIVA>{code}</CodigoTarifaIVA><Tarifa>{tarifa}</Tarifa><Monto>{monto:.5f}</Monto>{ex}</Impuesto>"
        neto = monto - exo
        lineas += (
            f"<LineaDetalle><NumeroLinea>{i}</NumeroLinea><CodigoCABYS>2399999999999</CodigoCABYS><Cantidad>1.000</Cantidad><UnidadMedida>Unid</UnidadMedida>"
            f"<Detalle>{det}</Detalle><PrecioUnitario>{sub:.5f}</PrecioUnitario><MontoTotal>{sub:.5f}</MontoTotal><SubTotal>{sub:.5f}</SubTotal>"
            f"<BaseImponible>{sub:.5f}</BaseImponible>{imp}<ImpuestoNeto>{neto:.5f}</ImpuestoNeto><MontoTotalLinea>{sub + neto:.5f}</MontoTotalLinea></LineaDetalle>"
        )
    desglose = "".join(
        f"<TotalDesgloseImpuesto><Codigo>01</Codigo><CodigoTarifaIVA>{c}</CodigoTarifaIVA><TotalMontoImpuesto>{m:.5f}</TotalMontoImpuesto></TotalDesgloseImpuesto>"
        for c, m in (("02", 100), ("03", 100), ("04", 400), ("08", 5200))
    )
    return (
        f'<?xml version="1.0" encoding="utf-8"?><FacturaElectronica xmlns="{ns}"><Clave>{_cl(n)}</Clave><CodigoActividadEmisor>4711.0</CodigoActividadEmisor>'
        f"<NumeroConsecutivo>0010000101{n:010d}</NumeroConsecutivo><FechaEmision>{FECHA_HOY}</FechaEmision>"
        f"<Emisor><Nombre>{nombre}</Nombre><Identificacion><Tipo>02</Tipo><Numero>{emisor}</Numero></Identificacion><CorreoElectronico>facturas@super.example</CorreoElectronico></Emisor>"
        f"<Receptor><Nombre>Empresa Receptora SA</Nombre><Identificacion><Tipo>02</Tipo><Numero>{EMPRESA}</Numero></Identificacion></Receptor>"
        f"<CondicionVenta>01</CondicionVenta><DetalleServicio>{lineas}</DetalleServicio><ResumenFactura>"
        f"<CodigoTipoMoneda><CodigoMoneda>CRC</CodigoMoneda><TipoCambio>1</TipoCambio></CodigoTipoMoneda>"
        f"<TotalVenta>{BASE:.5f}</TotalVenta><TotalVentaNeta>{BASE:.5f}</TotalVentaNeta>{desglose}<TotalImpuesto>{IVA:.5f}</TotalImpuesto>"
        f"<TotalComprobante>{TOTAL:.5f}</TotalComprobante></ResumenFactura></FacturaElectronica>"
    )


def _up(client, xml: str):
    r = client.post("/reception/upload", files={"file": ("factura.xml", xml.encode(), "application/xml")})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _respond(client, rid, **body):
    body.setdefault("action", "aceptada")
    r = client.post(f"/reception/{rid}/respond", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _by(parts: list[dict]) -> dict:
    return {(p["kind"], D(str(p["rate"])).normalize()): p for p in parts}


def _sumas_cuadran(e: Expense, parts) -> None:
    assert sum((p.total for p in parts), D(0)) == expense_tax.q5(e.total)
    assert sum((p.tax for p in parts), D(0)) == expense_tax.q5(e.tax_amount)


def _balanced(entry) -> bool:
    debe = sum((ln.amount for ln in entry.lines if ln.side == "debe"), D(0))
    haber = sum((ln.amount for ln in entry.lines if ln.side == "haber"), D(0))
    return debe == haber


# ---------------------------------------------------------------- XML con varias tarifas
def test_xml_con_varias_tarifas_exento_y_exonerado(client, auth, db_session):
    d = _up(client, xml_super(1))
    det = _respond(client, d["id"])
    eid = det["expense_id"]
    e = db_session.get(Expense, eid)
    assert D(str(e.total)) == TOTAL and D(str(e.tax_amount)) == IVA

    r = client.get(f"/expenses/{eid}/tax-breakdown")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["source"] == "xml" and out["stored"] is True and out["currency"] == "CRC"
    p = _by(out["parts"])
    assert set(p) == {("gravado", D(13)), ("gravado", D(4)), ("gravado", D(2)), ("gravado", D(1)), ("exonerado", D(13)), ("exento", D(0))}
    g13 = p[("gravado", D(13))]
    assert D(str(g13["base"])) == D(40000) and D(str(g13["tax"])) == D(5200) and D(str(g13["acreditable"])) == D(5200) and g13["rate_code"] == "08"
    assert D(str(p[("gravado", D(4))]["tax"])) == D(400) and D(str(p[("gravado", D(1))]["base"])) == D(10000)
    exo = p[("exonerado", D(13))]
    assert D(str(exo["base"])) == D(20000) and D(str(exo["tax"])) == 0 and D(str(exo["exonerated"])) == D(2600)
    ex = p[("exento", D(0))]
    assert D(str(ex["base"])) == D(8000) and D(str(ex["tax"])) == 0 and D(str(ex["acreditable"])) == 0
    # las partes suman el gasto y el credito
    assert D(str(out["totals"]["total"])) == TOTAL and D(str(out["totals"]["acreditable"])) == IVA
    parts = expense_tax.tax_breakdown(db_session, e)
    _sumas_cuadran(e, parts)
    assert all(isinstance(x.base, Decimal) and isinstance(x.acreditable, Decimal) for x in parts)
    assert db_session.scalar(select(ExpenseTaxLine.id).where(ExpenseTaxLine.expense_id == eid)) is not None

    # D-104: compras por tarifa en el panel tributario (el credito por tarifa suma el credito del XML)
    iva = client.get("/accounting/tax-summary", params={"month": HOY.strftime("%Y-%m")}).json()["iva"]
    t = {(r["kind"], D(str(r["rate"])).normalize()): r for r in iva["compras_por_tarifa"]}
    assert D(str(t[("gravado", D(13))]["acreditable_crc"])) == D("5200.00") and D(str(t[("exento", D(0))]["base_crc"])) == D("8000.00")
    assert sum((D(str(r["acreditable_crc"])) for r in iva["compras_por_tarifa"]), D(0)) == D(str(iva["credito_crc"]))


def test_parcial_escala_y_prorrata(client, auth, db_session):
    d = _up(client, xml_super(2))
    det = _respond(client, d["id"], action="parcial", detail="Llegó la mitad del pedido", accepted_total=float(TOTAL / 2), accepted_tax=float(IVA / 2))
    e = db_session.get(Expense, det["expense_id"])
    parts = expense_tax.tax_breakdown(db_session, e)
    _sumas_cuadran(e, parts)
    p = {(x.kind, x.rate): x for x in parts}
    assert p[("gravado", D(13))].tax == D(2600) and p[("gravado", D(13))].base == D(20000)
    assert sum((x.acreditable for x in parts), D(0)) == D(2900)

    d3 = _up(client, xml_super(3))
    det3 = _respond(client, d3["id"], iva_condition="proporcional")
    e3 = db_session.get(Expense, det3["expense_id"])
    parts3 = expense_tax.tax_breakdown(db_session, e3)
    _sumas_cuadran(e3, parts3)
    assert all(x.acreditable == 0 for x in parts3)
    assert all(x.prorrata for x in parts3 if x.kind == "gravado") and not any(x.prorrata for x in parts3 if x.kind == "exento")


def test_nota_de_credito_resta_por_tarifa(db_session):
    """Una NC del proveedor (gasto negativo) da partes negativas y el asiento invertido, cuadrado."""
    from app.models import ReceivedDocument

    tid = db_session.scalar(select(Tenant.id))
    doc = ReceivedDocument(tenant_id=tid, clave=_cl(9), doc_type="NC", currency="CRC", total=TOTAL, tax_total=IVA, xml_document=xml_super(9), action="aceptada", status="respondido")
    db_session.add(doc)
    db_session.flush()
    e = Expense(
        tenant_id=tid, description="NC ficticia", date=HOY, currency="CRC", subtotal=-BASE, tax_rate=D("6.237"), tax_amount=-IVA, total=-TOTAL,
        iva_credit="credito", tax_credit_amount=-IVA, received_document_id=doc.id, status="registrado", fx_rate=1, purchase_type="credito",
    )
    db_session.add(e)
    db_session.commit()
    parts = expense_tax.tax_breakdown(db_session, e)
    _sumas_cuadran(e, parts)
    assert all(x.base <= 0 and x.tax <= 0 for x in parts) and sum((x.acreditable for x in parts), D(0)) == -IVA
    entry = js.build_expense_entry(db_session, e)
    assert _balanced(entry) and not any(ln.role_key == "redondeo" for ln in entry.lines)
    assert all(ln.side == "haber" for ln in entry.lines if ln.role_key == "iva_acreditable")


# ---------------------------------------------------------------- gasto manual
def _cat(db_session, tid: int, name: str) -> ExpenseCategory:
    c = ExpenseCategory(tenant_id=tid, name=name)
    db_session.add(c)
    db_session.commit()
    return c


def _sup(db_session, tid: int, name: str, tax_id: str) -> Supplier:
    s = Supplier(tenant_id=tid, name=name, tax_id=tax_id)
    db_session.add(s)
    db_session.commit()
    return s


def _gasto(client, **kw) -> dict:
    body = {"description": "Gasto ficticio", "date": HOY.isoformat(), "currency": "CRC", "subtotal": 10000, "tax_rate": 13, "iva_credit": "credito", **kw}
    r = client.post("/expenses", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_gasto_manual_una_parte_y_sigue_al_editar(client, auth, db_session):
    g = _gasto(client, subtotal=20000, tax_rate=4)
    out = client.get(f"/expenses/{g['id']}/tax-breakdown").json()
    assert out["source"] == "manual" and len(out["parts"]) == 1
    part = out["parts"][0]
    assert D(str(part["rate"])) == D(4) and D(str(part["base"])) == D(20000) and D(str(part["tax"])) == D(800) and D(str(part["acreditable"])) == D(800)
    # guardar y despues editar el gasto: lo guardado ya no suma y se recalcula (nunca un desglose que no cuadra)
    assert client.post(f"/expenses/{g['id']}/tax-breakdown/recalcular").json()["changed"] is True
    body = {"description": "Gasto ficticio", "date": HOY.isoformat(), "currency": "CRC", "subtotal": 10000, "tax_rate": 13, "iva_credit": "no_credito"}
    assert client.put(f"/expenses/{g['id']}", json=body).status_code == 200
    out = client.get(f"/expenses/{g['id']}/tax-breakdown").json()
    assert out["stored"] is False and D(str(out["parts"][0]["rate"])) == D(13) and D(str(out["parts"][0]["acreditable"])) == 0
    sin_iva = _gasto(client, subtotal=5000, tax_rate=0, iva_credit="no_credito")
    assert client.get(f"/expenses/{sin_iva['id']}/tax-breakdown").json()["parts"][0]["kind"] == "exento"


# ---------------------------------------------------------------- asiento balanceado por tarifa
def _map(db_session, tid: int, roles: list[str]) -> None:
    acc = QboAccount(tenant_id=tid, full_name="Cuenta ficticia", active=True, imported_at=datetime.now(UTC))
    db_session.add(acc)
    db_session.flush()
    for r in roles:
        db_session.add(AccountingRoleMapping(tenant_id=tid, role_key=r, qbo_account_id=acc.id, updated_at=datetime.now(UTC)))
    db_session.commit()


def test_asiento_balanceado_por_tarifa(client, auth, db_session):
    tid = auth["tenant"]["id"]
    d = _up(client, xml_super(4))
    e = db_session.get(Expense, _respond(client, d["id"])["expense_id"])
    # sin cuentas por tarifa: todo el credito al IVA acreditable general, una linea por tarifa, cuadrado sin redondeo
    entry = js.build_expense_entry(db_session, e)
    assert _balanced(entry) and not any(ln.role_key == "redondeo" for ln in entry.lines)
    cred = [ln for ln in entry.lines if ln.role_key == "iva_acreditable"]
    assert sum((ln.amount for ln in cred), D(0)) == IVA and len(cred) == 4  # 13, 4, 2 y 1 %
    assert sum((ln.amount for ln in entry.lines if ln.role_key == "otros_ingresos"), D(0)) == BASE  # sin categoria
    # con la cuenta del 13 % mapeada, ese IVA va a su cuenta
    _map(db_session, tid, ["iva_acreditable_13", "iva_no_acreditable_4"])
    entry = js.build_expense_entry(db_session, e)
    assert _balanced(entry)
    assert sum((ln.amount for ln in entry.lines if ln.role_key == "iva_acreditable_13"), D(0)) == D(5200)
    assert sum((ln.amount for ln in entry.lines if ln.role_key == "iva_acreditable"), D(0)) == D(600)
    je = js.upsert_entry(db_session, entry)
    db_session.commit()
    assert sum((D(str(x.amount)) for x in je.lines if x.side == "debe"), D(0)) == sum((D(str(x.amount)) for x in je.lines if x.side == "haber"), D(0))

    # sin credito (04): el IVA no acreditable del 4 % va a su cuenta; el resto se queda en el gasto
    d5 = _up(client, xml_super(5))
    e5 = db_session.get(Expense, _respond(client, d5["id"], iva_condition="no_credito")["expense_id"])
    entry = js.build_expense_entry(db_session, e5)
    assert _balanced(entry) and not any(ln.role_key.startswith("iva_acreditable") for ln in entry.lines)
    assert [ln.amount for ln in entry.lines if ln.role_key == "iva_no_acreditable_4"] == [D(400)]
    assert sum((ln.amount for ln in entry.lines if ln.side == "debe"), D(0)) == TOTAL

    # gasto manual a una tarifa: el asiento de siempre (gasto + IVA acreditable vs banco/CxP)
    g = db_session.get(Expense, _gasto(client, subtotal=10000, tax_rate=13)["id"])
    entry = js.build_expense_entry(db_session, g)
    assert _balanced(entry) and [ln.amount for ln in entry.lines if ln.role_key == "iva_acreditable_13"] == [D(1300)]


# ---------------------------------------------------------------- relleno idempotente
def test_relleno_simula_aplica_e_idempotente(client, auth, db_session):
    tid = auth["tenant"]["id"]
    d = _up(client, xml_super(6))
    eid = _respond(client, d["id"])["expense_id"]
    g = _gasto(client, subtotal=1000, tax_rate=2)
    # como un gasto de antes de esta version: sin desglose guardado
    db_session.query(ExpenseTaxLine).delete()
    db_session.commit()

    sim = desglose_iva_gastos.run(db_session, tid)
    assert sim["aplicado"] is False and sim["nuevos"] == 1 and sim["con_varias_tarifas"] == 1 and not sim["errores"]
    assert db_session.query(ExpenseTaxLine).count() == 0  # simular no escribe

    ok = desglose_iva_gastos.run(db_session, tid, apply=True)
    assert ok["nuevos"] == 1 and db_session.query(ExpenseTaxLine).filter(ExpenseTaxLine.expense_id == eid).count() == 6
    assert db_session.query(ExpenseTaxLine).filter(ExpenseTaxLine.expense_id == g["id"]).count() == 0  # sin --manuales

    otra = desglose_iva_gastos.run(db_session, tid, apply=True)
    assert otra.get("nuevos", 0) == 0 and otra.get("actualizados", 0) == 0 and otra["sin_cambio"] == 1
    ids = sorted(db_session.scalars(select(ExpenseTaxLine.id)))

    man = desglose_iva_gastos.run(db_session, tid, apply=True, manuales=True)
    assert man["nuevos"] == 1 and man["sin_cambio"] == 1
    assert sorted(db_session.scalars(select(ExpenseTaxLine.id)))[: len(ids)] == ids  # lo del XML no se reescribio
    assert desglose_iva_gastos.run(db_session, tid, apply=True, manuales=True)["sin_cambio"] == 2


# ---------------------------------------------------------------- categoria automatica
def test_categoria_aprendida_por_proveedor(client, auth, db_session):
    tid = auth["tenant"]["id"]
    med = _cat(db_session, tid, "Medicamentos ficticios")
    lim = _cat(db_session, tid, "Limpieza ficticia")
    farm = _sup(db_session, tid, "Farmacia Ficticia SA", "3101888888")

    g1 = _gasto(client, supplier_id=farm.id, category_id=med.id)
    assert g1["category_id"] == med.id
    rule = db_session.scalar(select(SupplierCategoryRule).where(SupplierCategoryRule.supplier_id == farm.id))
    assert rule is not None and rule.category_id == med.id  # aprendio del primer gasto

    g2 = _gasto(client, supplier_id=farm.id)
    assert g2["category_id"] == med.id
    assert client.get(f"/expenses/{g2['id']}/tax-breakdown").json()["category_auto"] is True

    # la persona la corrige: deja de ser automatica y la regla aprende
    body = {"description": "Gasto ficticio", "date": HOY.isoformat(), "currency": "CRC", "subtotal": 10000, "tax_rate": 13, "iva_credit": "credito", "supplier_id": farm.id}
    assert client.put(f"/expenses/{g2['id']}", json={**body, "category_id": lim.id}).status_code == 200
    db_session.expire_all()
    assert db_session.get(Expense, g2["id"]).category_auto is False
    assert db_session.scalar(select(SupplierCategoryRule.category_id).where(SupplierCategoryRule.supplier_id == farm.id)) == lim.id
    g3 = _gasto(client, supplier_id=farm.id)
    assert g3["category_id"] == lim.id
    # confirmar la automatica la deja como de una persona
    r = client.post(f"/expenses/{g3['id']}/category/confirmar")
    assert r.status_code == 200 and r.json()["category_auto"] is False

    # sin regla todavia: la del ultimo gasto categorizado del proveedor (datos de antes de las reglas)
    viejo = _sup(db_session, tid, "Ferreteria Ficticia SA", "3101999999")
    db_session.add(Expense(tenant_id=tid, supplier_id=viejo.id, category_id=lim.id, description="Viejo", date=HOY, subtotal=1, total=1, tax_amount=0, tax_rate=0))
    db_session.commit()
    db_session.query(SupplierCategoryRule).filter(SupplierCategoryRule.supplier_id == viejo.id).delete()
    db_session.commit()
    assert _gasto(client, supplier_id=viejo.id)["category_id"] == lim.id

    # una regla fijada a mano manda sobre el historial; tambien al recibir un XML del proveedor
    sup_xml = _sup(db_session, tid, "Supermercado Ficticio SA", "3101777777")
    assert client.put(f"/expense-category-rules/{sup_xml.id}", json={"category_id": med.id}).status_code == 200
    d = _up(client, xml_super(7))
    e = db_session.get(Expense, _respond(client, d["id"])["expense_id"])
    assert e.supplier_id == sup_xml.id and e.category_id == med.id and e.category_auto is True
    rules = client.get("/expense-category-rules").json()
    assert any(r["supplier_id"] == sup_xml.id and r["source"] == "manual" and r["hits"] >= 1 for r in rules)
    assert client.delete(f"/expense-category-rules/{sup_xml.id}").status_code == 200


def test_aislamiento_entre_empresas(client, auth, db_session):
    tid = auth["tenant"]["id"]
    cat = _cat(db_session, tid, "Medicamentos ficticios")
    sup = _sup(db_session, tid, "Farmacia Ficticia SA", "3101888888")
    _gasto(client, supplier_id=sup.id, category_id=cat.id)  # regla en la empresa A

    otra = Tenant(slug="otra-iva-tarifa", name="Otra empresa de prueba", default_currency="CRC", settings={})
    db_session.add(otra)
    db_session.flush()
    cat_b = ExpenseCategory(tenant_id=otra.id, name="Categoria ajena")
    sup_b = Supplier(tenant_id=otra.id, name="Farmacia Ficticia SA", tax_id="3101888888")  # misma cedula, otra empresa
    db_session.add_all([cat_b, sup_b])
    db_session.flush()
    e_b = Expense(tenant_id=otra.id, supplier_id=sup_b.id, description="Gasto ajeno", date=HOY, subtotal=100, tax_rate=13, tax_amount=13, total=113)
    # un gasto de la empresa B con el proveedor de la A (dato malo) tampoco toma la regla de la A
    e_raro = Expense(tenant_id=otra.id, supplier_id=sup.id, description="Gasto raro", date=HOY, subtotal=100, tax_rate=0, tax_amount=0, total=100)
    db_session.add_all([e_b, e_raro])
    db_session.commit()
    assert e_b.category_id is None and e_raro.category_id is None
    assert db_session.scalar(select(SupplierCategoryRule).where(SupplierCategoryRule.tenant_id == otra.id)) is None

    # la API de la empresa A no ve ni toca lo de la B
    assert client.get(f"/expenses/{e_b.id}/tax-breakdown").status_code == 404
    assert client.post(f"/expenses/{e_b.id}/tax-breakdown/recalcular").status_code == 404
    assert client.post(f"/expenses/{e_b.id}/category/confirmar").status_code == 404
    assert client.put(f"/expense-category-rules/{sup_b.id}", json={"category_id": cat.id}).status_code == 404
    assert client.put(f"/expense-category-rules/{sup.id}", json={"category_id": cat_b.id}).status_code == 404
    assert all(r["supplier_id"] != sup_b.id for r in client.get("/expense-category-rules").json())
    # el relleno de una empresa no toca la otra
    desglose_iva_gastos.run(db_session, otra.id, apply=True, manuales=True)
    assert db_session.query(ExpenseTaxLine).filter(ExpenseTaxLine.tenant_id == tid).count() == 0
    assert db_session.query(ExpenseTaxLine).filter(ExpenseTaxLine.tenant_id == otra.id).count() == 2
