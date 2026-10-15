"""Recepcion de comprobantes: carga, emparejamiento, estados, respuesta (mensaje receptor), gasto ligado o vinculado,
descargas, permisos, plazos, modo pruebas con FakeHacienda y calculo del IVA acreditable."""

import io
import zipfile
from datetime import date
from decimal import Decimal

import pytest

from app.services.reception_calc import CalcError, business_days_left, compute, deadline
from tests.hacienda_utils import (
    EMPRESA,
    PDF,
    FakeHacienda,
    clave,
    fe_xml,
    mensaje_hacienda,
    mensaje_receptor,
    modo_pruebas,
    role_headers,
)

HOY = date.today()
FECHA_HOY = f"{HOY.isoformat()}T10:00:00-06:00"
DDMMAA = HOY.strftime("%d%m%y")


def cl(n: int = 1, emisor: str = "003101000001") -> str:
    return clave(fecha=DDMMAA, emisor=emisor, consecutivo=f"0010000101{n:010d}")


def xml_hoy(n: int = 1, **kw) -> str:
    kw.setdefault("fecha", FECHA_HOY)
    kw.setdefault("consecutivo", f"0010000101{n:010d}")
    return fe_xml(cl(n), **kw)


def up(client, xml: str | bytes, name: str = "factura.xml", headers=None):
    data = xml.encode() if isinstance(xml, str) else xml
    return client.post("/reception/upload", files={"file": (name, data, "application/xml")}, headers=headers)


def batch(client, files: list[tuple[str, bytes]]):
    return client.post("/reception/upload-batch", files=[("files", (n, b, "application/octet-stream")) for n, b in files])


def respond(client, rid, **body):
    body.setdefault("action", "aceptada")
    return client.post(f"/reception/{rid}/respond", json=body)


# ---------------------------------------------------------------- carga y emparejamiento
def test_carga_duplicado_invalido_y_respuesta_suelta(client, auth):
    r = up(client, xml_hoy(1))
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["status"] == "por_revisar" and d["doc_type"] == "FE" and d["hacienda_status"] == "sin_respuesta"
    assert d["deadline"] and d["receiver_ok"] is True and d["origin"] == "manual"
    assert up(client, xml_hoy(1)).status_code == 409
    assert up(client, "<xml/>").status_code == 422
    assert up(client, mensaje_hacienda(cl(9))).status_code == 422  # sin comprobante: llegara con el
    r = up(client, mensaje_hacienda(cl(1)), name="respuesta.xml")
    assert r.status_code == 200 and r.json()["adjuntos"] == [{"kind": "respuesta", "id": d["id"]}]
    det = client.get(f"/reception/{d['id']}").json()
    assert det["has_supplier_response"] and det["supplier_hacienda_status"] == "aceptado"


def test_lote_xml_pdf_respuesta_zip_y_pdf_por_nombre(client, auth):
    r = batch(client, [("f2.xml", xml_hoy(2).encode()), ("f2.pdf", PDF), ("resp.xml", mensaje_hacienda(cl(2)).encode())])
    assert r.status_code == 200, r.text
    res = r.json()
    assert len(res["nuevos"]) == 1 and {a["kind"] for a in res["adjuntos"]} == {"pdf", "respuesta"}
    rid = res["nuevos"][0]
    det = client.get(f"/reception/{rid}").json()
    assert det["has_pdf"] and det["has_supplier_response"]
    # zip con dos comprobantes y un PDF por clave en el nombre
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.xml", xml_hoy(3))
        z.writestr("b.xml", xml_hoy(4))
        z.writestr(f"factura-{cl(4)}.pdf", PDF + b"4")
    res = batch(client, [("todo.zip", buf.getvalue())]).json()
    assert len(res["nuevos"]) == 2 and res["adjuntos"] == [{"kind": "pdf", "id": max(res["nuevos"])}] or len(res["adjuntos"]) == 1
    # PDF que llega despues, en otro lote, se empareja por la clave del nombre
    up(client, xml_hoy(5))
    res = batch(client, [(f"{cl(5)}.pdf", PDF + b"5")]).json()
    assert res["adjuntos"] and res["adjuntos"][0]["kind"] == "pdf"
    # PDF sin pareja queda sin asignar (se ve en Correos)
    res = batch(client, [("cualquiera.pdf", PDF + b"x")]).json()
    assert res["sin_asignar"] and not res["adjuntos"]
    msgs = client.get("/reception/messages").json()
    assert msgs["total"] >= 4 and msgs["items"][0]["source"] == "manual"


def test_zip_bomba_y_rutas_raras(client, auth):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("bomba.xml", b"<x>" + b"0" * (11 * 1024 * 1024) + b"</x>")
    res = batch(client, [("bomba.zip", buf.getvalue())]).json()
    assert res["errores"] and "zip" in res["errores"][0]["error"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../../etc/passwd.xml", xml_hoy(6))
    res = batch(client, [("ruta.zip", buf.getvalue())]).json()
    assert res["errores"] and not res["nuevos"]


def test_estados_iniciales(client, auth):
    ajeno = up(client, xml_hoy(7, receptor="3101999999")).json()
    assert ajeno["status"] == "ajeno"
    assert respond(client, ajeno["id"]).status_code == 409
    sin_rec = up(client, xml_hoy(8, receptor=None)).json()
    assert sin_rec["status"] == "ajeno"
    propio = up(client, fe_xml(cl(9, emisor="003102000000"), emisor=EMPRESA, fecha=FECHA_HOY))
    assert propio.status_code == 422 and "emitido por la empresa" in propio.json()["detail"]
    # respuesta del proveedor rechazada -> invalido
    d = up(client, xml_hoy(10)).json()
    up(client, mensaje_hacienda(cl(10), mensaje="3", detalle="Rechazado"))
    det = client.get(f"/reception/{d['id']}").json()
    assert det["status"] == "invalido" and det["supplier_hacienda_status"] == "rechazado"
    assert respond(client, d["id"]).status_code == 409
    # tiquete: sin mensaje receptor; se registra el gasto sin credito
    te = up(client, fe_xml(cl(11), tipo="TiqueteElectronico", receptor=None, fecha=FECHA_HOY)).json()
    assert te["status"] == "sin_respuesta"
    g = client.post(f"/reception/{te['id']}/expense", json={})
    assert g.status_code == 201, g.text
    e = next(x for x in client.get("/expenses").json() if x["id"] == g.json()["expense_id"])
    assert e["iva_credit"] == "no_credito" and Decimal(str(e["tax_credit_amount"])) == 0 and e["locked"]


# ---------------------------------------------------------------- responder (simulado)
@pytest.mark.parametrize(
    "cond,credit,expected_credit",
    [("credito", None, "13000"), ("credito_parcial", 5000, "5000"), ("bienes_capital", None, "13000"), ("no_credito", None, "0"), ("proporcional", None, None)],
)
def test_responder_condiciones(client, auth, cond, credit, expected_credit):
    d = up(client, xml_hoy(20)).json()
    r = respond(client, d["id"], iva_condition=cond, credit_amount=credit)
    assert r.status_code == 200, r.text
    det = r.json()
    assert det["status"] == "respondido" and det["iva_condition"] == cond and det["hacienda_status"] == "simulado"
    assert det["hacienda_status"] not in ("aprobado", "aceptado")  # simulado nunca se ve como aceptado
    assert (det["credit_amount"] is None and expected_credit is None) or Decimal(str(det["credit_amount"])) == Decimal(expected_credit)
    if expected_credit is not None:
        assert Decimal(str(det["credit_amount"])) + Decimal(str(det["applicable_amount"])) == Decimal("113000")
    resp = det["responses"][0]
    assert resp["consecutive"] == "00100001" + "05" + "0000000001" and resp["mode"] == "simulado" and resp["status"] == "simulado"
    assert det["activity_code"] == ("6202.0" if cond != "proporcional" else None) or cond == "proporcional"
    mr = client.get(f"/reception/{d['id']}/file/mr")
    assert mr.status_code == 200 and b"MensajeReceptor" in mr.content and b"ds:Signature" in mr.content or b"Signature" in mr.content


def test_parcial_rechazo_y_actividad(client, auth):
    d = up(client, xml_hoy(21)).json()
    assert respond(client, d["id"], action="parcial", accepted_total=50000, accepted_tax=5000).status_code == 422  # sin detalle
    assert respond(client, d["id"], action="parcial", detail="Faltó un equipo").status_code == 422  # sin montos
    assert respond(client, d["id"], action="parcial", detail="Faltó un equipo", accepted_total=50000, accepted_tax=20000).status_code == 422  # IVA > IVA del comprobante
    assert respond(client, d["id"], activity_code="9999.9").status_code == 422  # no esta en Ajustes
    assert respond(client, d["id"], detail="abc").status_code == 422  # detalle de menos de 5 tambien en aceptacion
    r = respond(client, d["id"], action="parcial", detail="Faltó un equipo", accepted_total=56500, accepted_tax=6500)
    assert r.status_code == 200, r.text
    assert r.json()["responses"][0]["consecutive"][8:10] == "06" and Decimal(str(r.json()["credit_amount"])) == Decimal("6500")
    rech = up(client, xml_hoy(22)).json()
    r = respond(client, rech["id"], action="rechazada", detail="No corresponde a un pedido")
    assert r.status_code == 200 and r.json()["expense_id"] is None and r.json()["responses"][0]["consecutive"][8:10] == "07"
    assert respond(client, rech["id"], action="rechazada", detail="Otra vez no").status_code == 409


def test_gasto_con_proveedor_usd_bloqueado_y_nc(client, auth, db_session):
    from app.models import Supplier

    d = up(client, xml_hoy(23, currency="USD", fx="512.35000", total=226, iva=26)).json()
    r = respond(client, d["id"])
    eid = r.json()["expense_id"]
    e = next(x for x in client.get("/expenses").json() if x["id"] == eid)
    assert e["received_document_id"] == d["id"] and e["currency"] == "USD" and Decimal(str(e["fx_rate"])) == Decimal("512.35")
    assert Decimal(str(e["tax_credit_amount"])) == Decimal("26") and e["supplier_name"] == "Distribuidora Ficticia SA" and e["origin"] == "xml"
    # segundo comprobante del mismo proveedor: reutiliza el proveedor
    d2 = up(client, xml_hoy(24)).json()
    respond(client, d2["id"])
    assert db_session.query(Supplier).filter(Supplier.tax_id == "3101000001").count() == 1
    # editar montos de un gasto de XML -> 409; la categoria si se edita
    cat = client.get("/expense-categories").json()[0]
    body = {"description": e["description"], "date": e["date"], "currency": "USD", "subtotal": e["subtotal"], "tax_rate": e["tax_rate"], "iva_credit": e["iva_credit"]}
    assert client.put(f"/expenses/{eid}", json={**body, "subtotal": 1}).status_code == 409
    ok = client.put(f"/expenses/{eid}", json={**body, "category_id": cat["id"], "subtotal": float(e["subtotal"])})
    assert ok.status_code == 200 and ok.json()["category_id"] == cat["id"] and Decimal(str(ok.json()["total"])) == Decimal("226")
    # NC del proveedor -> gasto negativo
    nc = up(client, fe_xml(clave(fecha=DDMMAA, consecutivo="00100001030000000077"), tipo="NotaCreditoElectronica", referencia=cl(24), total=1130, iva=130, fecha=FECHA_HOY)).json()
    r = respond(client, nc["id"])
    g = next(x for x in client.get("/expenses").json() if x["id"] == r.json()["expense_id"])
    assert Decimal(str(g["total"])) == Decimal("-1130") and g["description"].startswith("Nota de crédito")


def test_descartar_restaurar_y_descargas(client, auth):
    d = up(client, xml_hoy(25)).json()
    assert client.post(f"/reception/{d['id']}/discard", json={"reason": "No es nuestro"}).json()["status"] == "descartado"
    assert client.post(f"/reception/{d['id']}/restore").json()["status"] == "por_revisar"
    batch(client, [(f"{cl(25)}.pdf", PDF + b"25"), ("r.xml", mensaje_hacienda(cl(25)).encode())])
    respond(client, d["id"])
    assert client.post(f"/reception/{d['id']}/discard", json={"reason": "Ya no"}).status_code == 409  # respondido: se conserva
    for which, ctype in (("document", "xml"), ("supplier_response", "xml"), ("mr", "xml"), ("hacienda", "xml"), ("pdf", "pdf")):
        f = client.get(f"/reception/{d['id']}/file/{which}")
        assert f.status_code == 200 and ctype in f.headers["content-type"], which
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/reception/{d['id']}/file/todo").content))
    c = cl(25)
    assert set(z.namelist()) == {f"{c}.xml", f"{c}-respuesta-proveedor.xml", f"{c}-mensaje-receptor.xml", f"{c}-respuesta-hacienda.xml", f"{c}.pdf"}
    assert client.get(f"/reception/{d['id']}/xml/response").status_code == 200  # alias viejo


def test_permisos_lista_detalle_y_plazos(client, auth, db_session):
    tid = auth["tenant"]["id"]
    for n in (30, 31, 32):
        up(client, xml_hoy(n, nombre=f"Proveedor {n} SA"))
    ventas = role_headers(client, db_session, tid, "ventas.rec@ejemplo.com", "ventas")
    lectura = role_headers(client, db_session, tid, "lectura.rec@ejemplo.com", "lectura")
    conta = role_headers(client, db_session, tid, "conta.rec@ejemplo.com", "contabilidad")
    for h in (ventas, lectura):
        assert client.get("/reception/documents", headers=h).status_code == 403
        assert up(client, xml_hoy(33), headers=h).status_code == 403
    page = client.get("/reception/documents", params={"limit": 2}, headers=conta).json()
    assert page["total"] == 3 and len(page["items"]) == 2 and page["counts"]["por_revisar"] == 3
    assert client.get("/reception/documents", params={"q": "Proveedor 31"}, headers=conta).json()["total"] == 1
    assert client.get("/reception/documents", params={"status": "respondido"}, headers=conta).json()["total"] == 0
    rid = page["items"][0]["id"]
    det = client.get(f"/reception/{rid}", headers=conta).json()
    assert det["lines"] and not ({"product_id", "cost", "margin"} & set(det["lines"][0])) and "cost" not in det
    assert respond(client, rid).status_code == 200
    s = client.get("/reception/summary", headers=conta).json()
    assert s["por_revisar"] == 2


def test_plazo_8_dias_habiles():
    assert deadline(date(2026, 10, 9)) == date(2026, 11, 11)
    assert deadline(date(2026, 9, 30)) == date(2026, 10, 12)
    assert deadline(date(2026, 12, 15)) == date(2027, 1, 12)
    assert deadline(date(2027, 1, 31)) == date(2027, 2, 10)
    assert business_days_left(date(2026, 11, 9), date(2026, 11, 11)) == 2
    assert business_days_left(date(2026, 11, 12), date(2026, 11, 11)) == -1


def test_migracion_usa_el_mismo_plazo():
    import importlib.util
    from pathlib import Path

    path = next(Path(__file__).resolve().parents[1].joinpath("alembic", "versions").glob("facturacion_*.py"))
    spec = importlib.util.spec_from_file_location("fact_mig", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for x in (date(2026, 10, 9), date(2026, 9, 30), date(2026, 12, 15), date(2027, 1, 31), date(2028, 2, 29)):
        assert mod.deadline(x) == deadline(x)


# ---------------------------------------------------------------- calculo
def test_calculo_tabla_e_invariantes():
    I, V, T = Decimal("15000"), Decimal("13000"), Decimal("115000")  # noqa: N806 - nombres de la spec
    c = compute("aceptada", "01", I, V, T)
    assert c.credit == V and c.applicable == T - V and c.xml_amounts  # hay impuesto que no es IVA
    assert compute("aceptada", "01", V, V, T).xml_amounts is False
    c = compute("aceptada", "02", I, V, T, credit_given=Decimal("5000"))
    assert c.credit == Decimal("5000") and c.applicable == T - Decimal("5000") and c.xml_amounts
    with pytest.raises(CalcError):
        compute("aceptada", "02", I, V, T)
    assert compute("aceptada", "03", I, V, T).credit == V
    c = compute("aceptada", "04", I, V, T)
    assert c.credit == 0 and c.applicable == T
    c = compute("aceptada", "05", I, V, T)
    assert c.credit is None and c.applicable is None and not c.xml_amounts
    c = compute("parcial", "01", I, V, T, Decimal("50000"), Decimal("6000"))
    assert c.credit == Decimal("6000") and c.credit + c.applicable == Decimal("50000") and c.xml_amounts
    with pytest.raises(CalcError):
        compute("parcial", "01", I, V, T, Decimal("50000"), Decimal("14000"))  # IVA aceptado > IVA del comprobante
    r = compute("rechazada", "01", I, V, T)
    assert r.credit is None and r.applicable is None and not r.xml_amounts
    for cond in ("01", "03", "04"):
        c = compute("aceptada", cond, I, V, T)
        assert 0 <= c.credit <= c.base_iva <= I and c.credit + c.applicable == c.base_total


def test_fe_con_impuesto_no_iva_lleva_montos(client, auth):
    d = up(client, xml_hoy(40, iva=13000, impuesto_especifico=2000, total=115000)).json()
    r = respond(client, d["id"])
    assert Decimal(str(r.json()["credit_amount"])) == Decimal("13000")
    mr = client.get(f"/reception/{d['id']}/file/mr").content
    assert b"MontoTotalImpuestoAcreditar>13000.00000<" in mr and b"MontoTotalImpuesto>15000.00000<" in mr


def test_doble_respuesta_un_solo_mensaje(client, auth, db_session):
    from sqlalchemy.exc import IntegrityError

    from app.models import BillingGroup, ReceptionResponse

    d = up(client, xml_hoy(41)).json()
    assert respond(client, d["id"]).status_code == 200
    assert respond(client, d["id"]).status_code == 409  # doble clic
    assert db_session.query(ReceptionResponse).filter(ReceptionResponse.received_document_id == d["id"]).count() == 1
    assert db_session.query(BillingGroup).filter(BillingGroup.doc_type == "T05").one().current == 1
    # el unico (documento, intento) frena a dos procesos que calcularon el mismo intento
    r1 = db_session.query(ReceptionResponse).filter(ReceptionResponse.received_document_id == d["id"]).one()
    dup = ReceptionResponse(
        tenant_id=r1.tenant_id, received_document_id=r1.received_document_id, attempt=1, message_code="1", consecutive="00100001050000009999",
        mode="simulado", emitted_at=r1.emitted_at, status="simulado", tax_total=0, total=0,
    )
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_nc_que_ajusta_parcial_se_propone_sin_gasto(client, auth):
    f = up(client, xml_hoy(42)).json()
    respond(client, f["id"], action="parcial", detail="Faltó un equipo", accepted_total=56500, accepted_tax=6500)
    nc_clave = clave(fecha=DDMMAA, consecutivo="00100001030000000042")
    nc = up(client, fe_xml(nc_clave, tipo="NotaCreditoElectronica", referencia=cl(42), total=56500, iva=6500, fecha=FECHA_HOY)).json()
    det = client.get(f"/reception/{nc['id']}").json()
    assert det["proposal"] == {"action": "aceptada", "iva_condition": "no_credito", "create_expense": False, "reference_id": f["id"]}
    assert any("corrige una factura" in w for w in det["warnings"])
    r = respond(client, nc["id"], iva_condition="no_credito", create_expense=False)
    assert r.status_code == 200 and r.json()["expense_id"] is None
    month = HOY.strftime("%Y-%m")
    tax = client.get("/accounting/tax-summary", params={"month": month}).json()["iva"]
    assert Decimal(str(tax["credito_xml_crc"])) == Decimal("6500.00")  # solo lo aceptado; la NC no resta otra vez
    gastos = [g for g in client.get("/expenses").json() if g["received_document_id"] in (f["id"], nc["id"])]
    assert len(gastos) == 1 and Decimal(str(gastos[0]["total"])) == Decimal("56500")


def test_vincular_gasto_recurrente_existente(client, auth):
    rec = client.post(
        "/recurrences",
        json={"name": "Internet", "kind": "gasto", "template": {"description": "Internet oficina", "subtotal": 30000, "tax_rate": 0}, "frequency": "mensual", "next_date": HOY.isoformat()},
    ).json()
    run = client.post(f"/recurrences/{rec['id']}/run").json()
    d = up(client, xml_hoy(43, total=30000, iva=0)).json()
    det = client.get(f"/reception/{d['id']}").json()
    assert det["expense_candidates"] and det["expense_candidates"][0]["id"] == run["expense_id"]
    antes = len(client.get("/expenses").json())
    r = respond(client, d["id"], link_expense_id=run["expense_id"])
    assert r.status_code == 200 and r.json()["expense_id"] == run["expense_id"]
    assert len(client.get("/expenses").json()) == antes  # no se duplico
    e = next(x for x in client.get("/expenses").json() if x["id"] == run["expense_id"])
    assert e["locked"] and e["received_document_id"] == d["id"] and Decimal(str(e["total"])) == Decimal("30000")
    d2 = up(client, xml_hoy(44, total=30000, iva=0)).json()
    assert respond(client, d2["id"], link_expense_id=run["expense_id"]).status_code == 409  # ya ligado a otra


def test_mensaje_receptor_de_un_cliente(client, auth, db_session, monkeypatch):
    from app.models import EmailOutbox, EInvoiceDocument

    client.put("/settings", json={"einvoice_provider": "sandbox"})
    cust = client.get("/customers").json()["items"][0]
    prods = client.get("/products", params={"q": "domo"}).json()["items"]
    inv = client.post("/invoices", json={"customer_id": cust["id"], "lines": [{"product_id": prods[0]["id"], "quantity": 1}]}).json()
    em = client.post(f"/invoices/{inv['id']}/emit").json()
    antes = db_session.query(EmailOutbox).count()
    receptor = "".join(ch for ch in cust["id_number"] if ch.isdigit())  # la cedula del cliente de la factura
    # un tercero que conoce la clave (la cedula de la empresa es publica) no puede "rechazar" la factura en nombre del cliente
    r = up(client, mensaje_receptor(em["clave"], emisor=EMPRESA, receptor="3101999999", mensaje="3", detalle="Rechazo falso"), name="mr.xml")
    assert r.status_code == 422 and "cliente" in r.json()["detail"]
    doc = db_session.query(EInvoiceDocument).filter(EInvoiceDocument.clave == em["clave"]).one()
    assert doc.receiver_message is None and db_session.query(EmailOutbox).count() == antes
    detalle = 'No corresponde <a href="https://phish.example">aqui</a>'
    r = up(client, mensaje_receptor(em["clave"], emisor=EMPRESA, receptor=receptor, mensaje="3", detalle=detalle.replace("<", "&lt;")), name="mr.xml")
    assert r.status_code == 200 and r.json()["adjuntos"][0]["kind"] == "respuesta_cliente"
    db_session.refresh(doc)
    assert doc.receiver_message == "3" and doc.receiver_detail == detalle
    alertas = db_session.query(EmailOutbox).order_by(EmailOutbox.id).all()[antes:]
    assert alertas  # alerta: hay que emitir la NC
    assert all("<a href" not in m.html and "firma no verificada" in m.html for m in alertas)  # escapado y rotulado
    # el primero no se sobrescribe: un segundo mensaje (aceptacion) queda en la bitacora, no en la factura
    r = up(client, mensaje_receptor(em["clave"], emisor=EMPRESA, receptor=receptor, mensaje="1"), name="mr2.xml")
    assert r.status_code == 422 and "se conserva el primero" in r.json()["detail"]
    db_session.refresh(doc)
    assert doc.receiver_message == "3"
    xs = client.get(f"/invoices/{inv['id']}/xml").json()
    assert xs[0]["receiver_message"] == "3"


def test_proveedor_en_papelera_se_reutiliza(client, auth, db_session):
    from datetime import UTC, datetime

    from app.models import Supplier

    s = Supplier(tenant_id=auth["tenant"]["id"], name="Distribuidora Ficticia SA", tax_id="3-101-000001", trashed_at=datetime.now(UTC))
    db_session.add(s)
    db_session.commit()
    d = up(client, xml_hoy(45)).json()
    respond(client, d["id"])
    db_session.expire_all()
    assert db_session.query(Supplier).filter(Supplier.tenant_id == auth["tenant"]["id"]).filter(Supplier.name == "Distribuidora Ficticia SA").count() == 1
    assert db_session.get(Supplier, s.id).trashed_at is None


# ---------------------------------------------------------------- modo pruebas con FakeHacienda
def test_modo_pruebas_envio_consulta_y_reintento(client, auth, db_session, monkeypatch, p12_empresa):
    from app.models import Expense

    fake = FakeHacienda(sequence=["procesando", "aceptado"]).install(monkeypatch)
    modo_pruebas(client, *p12_empresa)
    d = up(client, xml_hoy(50)).json()
    r = respond(client, d["id"], send_now=False).json()
    assert r["hacienda_status"] == "por_enviar" and r["responses"][0]["mode"] == "pruebas" and r["responses"][0]["consecutive"][8:10] == "05"
    r = client.post(f"/reception/{d['id']}/send").json()
    assert r["hacienda_status"] == "enviado" and len(fake.posts) == 1
    body = fake.posts[0]
    assert body["clave"] == cl(50) and body["consecutivoReceptor"] == r["responses"][0]["consecutive"] and body["receptor"]["numeroIdentificacion"] == EMPRESA
    r = client.post(f"/reception/{d['id']}/refresh").json()
    assert r["hacienda_status"] == "procesando"
    r = client.post(f"/reception/{d['id']}/refresh").json()
    assert r["hacienda_status"] == "aceptado" and r["has_hacienda_answer"]
    # rechazado -> responder de nuevo con consecutivo nuevo; el gasto ligado se actualiza (y se anula si es rechazo)
    d2 = up(client, xml_hoy(51)).json()
    fake.final[f"{cl(51)}"] = "rechazado"
    r = respond(client, d2["id"]).json()  # send_now: sale de una vez
    first_cons = r["responses"][0]["consecutive"]
    ref = f"{cl(51)}-{first_cons}"
    fake.final[ref] = "rechazado"
    r = client.post(f"/reception/{d2['id']}/refresh").json()
    assert r["hacienda_status"] == "rechazado" and r["can_retry"]
    assert client.post(f"/reception/{d2['id']}/retry").json()["status"] == "por_revisar"
    r = respond(client, d2["id"], action="rechazada", detail="Precio distinto al pactado").json()
    assert r["responses"][-1]["consecutive"] != first_cons and r["responses"][-1]["consecutive"][8:10] == "07"
    e = db_session.get(Expense, r["expense_id"])
    db_session.refresh(e)
    assert e.status == "anulado"


def test_reabrir_solo_en_produccion(client, auth, monkeypatch):
    from app.services import hacienda_config

    d = up(client, xml_hoy(52)).json()
    respond(client, d["id"])
    assert client.post(f"/reception/{d['id']}/reopen", json={"confirm_not_in_fygaro": True}).status_code == 409  # simulado
    monkeypatch.setattr(hacienda_config, "reception_mode", lambda db, t: "produccion")
    assert client.post(f"/reception/{d['id']}/reopen", json={}).status_code == 422  # sin la casilla
    r = client.post(f"/reception/{d['id']}/reopen", json={"confirm_not_in_fygaro": True})
    assert r.status_code == 200 and r.json()["status"] == "por_revisar"
    viejo = up(client, fe_xml(clave(fecha="090926", consecutivo="00100001010000000053"), fecha="2026-09-09T10:00:00-06:00", consecutivo="00100001010000000053")).json()
    monkeypatch.setattr(hacienda_config, "reception_mode", lambda db, t: "simulado")
    respond(client, viejo["id"])
    monkeypatch.setattr(hacienda_config, "reception_mode", lambda db, t: "produccion")
    if viejo["deadline"] < HOY.isoformat():
        assert client.post(f"/reception/{viejo['id']}/reopen", json={"confirm_not_in_fygaro": True}).status_code == 409  # plazo vencido
