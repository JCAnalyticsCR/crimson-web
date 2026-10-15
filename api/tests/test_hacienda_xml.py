"""Lectura endurecida de comprobantes (4.4 / 4.3, con y sin prefijo) y construccion del mensaje receptor y de FE/TE/NC
en el orden del XSD oficial: todo lo que sale se firma y valida con 0 errores."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.providers.hacienda import xml44, xsd
from app.providers.hacienda.xades import sign_simulated
from tests.hacienda_utils import EMPRESA, clave, fe_xml, mensaje_hacienda


def parse(xml: str | bytes):
    data = xml.encode() if isinstance(xml, str) else xml
    root = xml44.parse_xml_bytes(data)
    return xml44.parse_comprobante(root, data)


def test_lee_44_con_y_sin_prefijo_y_43():
    c = clave()
    for prefix in (None, "ns0"):
        p = parse(fe_xml(c, prefix=prefix))
        assert p.doc_type == "FE" and p.schema_version == "4.4" and p.clave == c
        assert p.issuer_id == "3101000001" and p.receiver_id == EMPRESA and p.issuer_activity_code == "4651.0"
        assert p.total == Decimal("113000") and p.tax_total == Decimal("13000") and p.iva_total == Decimal("13000")
        assert p.issue_date.isoformat() == "2026-10-09" and p.issuer_email == "ventas@proveedor.example" and p.lines
    p = parse(fe_xml(c, version="4.3"))
    assert p.schema_version == "4.3" and p.issuer_activity_code == "465100" and p.iva_total == Decimal("13000")


def test_rechaza_dtd_entidades_y_tamano():
    c = clave()
    xxe = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>' + fe_xml(c).split("?>", 1)[1]
    with pytest.raises(xml44.XmlRejected, match="DTD"):
        xml44.parse_xml_bytes(xxe.encode())
    with pytest.raises(xml44.XmlRejected, match="DTD"):
        xml44.parse_xml_bytes(b'<?xml version="1.0"?><!ENTITY a "b"><x/>')
    with pytest.raises(xml44.XmlRejected, match="2 MB"):
        xml44.parse_xml_bytes(b"<x>" + b"a" * (2 * 1024 * 1024) + b"</x>")
    # UTF-16 con BOM tambien se revisa
    with pytest.raises(xml44.XmlRejected):
        xml44.parse_xml_bytes('<?xml version="1.0" encoding="utf-16"?><!DOCTYPE x><x/>'.encode("utf-16"))


def test_tiquete_nc_usd_otros_cargos_y_bom():
    c = clave()
    te = parse(fe_xml(c, tipo="TiqueteElectronico", receptor=None))
    assert te.doc_type == "TE" and te.receiver_id is None
    nc = parse(fe_xml(clave(consecutivo="00100001030000000009"), tipo="NotaCreditoElectronica", referencia=c))
    assert nc.doc_type == "NC" and nc.reference_clave == c
    usd = parse(fe_xml(c, currency="USD", fx="512.35000", total=226, iva=26))
    assert usd.currency == "USD" and usd.fx_rate == Decimal("512.35000")
    oc = parse(fe_xml(c, otros_cargos=1000, total=114000))
    assert oc.other_charges == Decimal("1000") and oc.net == Decimal("100000")
    bom = parse(b"\xef\xbb\xbf  " + fe_xml(c).encode())
    assert bom.clave == c


def test_mensaje_hacienda_y_avisos():
    c = clave()
    for msg, det in (("1", "Aceptado"), ("3", "Rechazado por la prueba")):
        root = xml44.parse_xml_bytes(mensaje_hacienda(c, mensaje=msg, detalle=det).encode())
        assert xml44.classify(root) == "mensaje_hacienda"
        mh = xml44.parse_mensaje_hacienda(root)
        assert mh["clave"] == c and mh["mensaje"] == msg and mh["detalle"] == det
    p = parse(fe_xml(clave(fecha="010126")))  # la clave dice 1 de enero, la fecha de emision 9 de octubre
    assert "La fecha de la clave no coincide con la fecha de emisión" in p.warnings


def test_iva_total_separado_de_otros_impuestos_y_cedula_alfanumerica():
    c = clave()
    p = parse(fe_xml(c, iva=13000, impuesto_especifico=2000, total=115000))
    assert p.tax_total == Decimal("15000") and p.iva_total == Decimal("13000") and p.iva_total < p.tax_total
    p = parse(fe_xml(c, emisor="3-101-AB12-34"))
    assert p.issuer_id == "3101AB1234"  # tal cual (sin separadores, mayusculas), sin quitarle letras
    assert xml44.same_id("3-102-000000", "3102000000") and not xml44.same_id("3101AB1234", "3101001234")


def _mr(code="1", cond="01", tax=Decimal("13000"), iva=Decimal("13000"), detail=None, emisor="3101000001", credit=None, applicable=None, action=None):
    from app.services.reception_calc import compute, xml_amounts

    action = action or {"1": "aceptada", "2": "parcial", "3": "rechazada"}[code]
    calc = None
    if action != "rechazada":
        calc = compute(action, cond, tax, iva, Decimal("113000"), Decimal("56500") if action == "parcial" else None, Decimal("6500") if action == "parcial" else None, credit)
    return xml44.mensaje_receptor_element(
        clave=clave(),
        issuer_id=emisor,
        emitted_at=datetime(2026, 10, 14, 23, 30, 0, 123456, tzinfo=xml44.CR),
        message_code=code,
        detail=detail,
        tax_total=tax,
        activity_code="6202.0",
        condition_code=cond if action != "rechazada" else None,
        credit=calc.credit if calc else None,
        applicable=calc.applicable if calc else None,
        include_amounts=xml_amounts(action, cond, tax, iva) if action != "rechazada" else False,
        total=Decimal("113000"),
        receiver_digits=EMPRESA,
        consecutive="00100001050000000001",
    )


def _names(root) -> list[str]:
    return [xml44.local(e) for e in root]


ORDER = [
    "Clave",
    "NumeroCedulaEmisor",
    "FechaEmisionDoc",
    "Mensaje",
    "DetalleMensaje",
    "MontoTotalImpuesto",
    "CodigoActividad",
    "CondicionImpuesto",
    "MontoTotalImpuestoAcreditar",
    "MontoTotalDeGastoAplicable",
    "TotalFactura",
    "NumeroCedulaReceptor",
    "NumeroConsecutivoReceptor",
]


def test_mensaje_receptor_orden_montos_y_xsd():
    casos = [
        ("1", "01", None, False),
        ("1", "02", Decimal("5000"), True),
        ("1", "03", None, True),
        ("1", "04", None, True),
        ("1", "05", None, False),
        ("2", "01", None, True),
        ("3", None, None, False),
    ]
    for code, cond, credit, amounts in casos:
        root = _mr(code, cond or "01", credit=credit, detail="Detalle de prueba" if code != "1" else None)
        names = _names(root)
        assert names == [n for n in ORDER if n in names], names  # orden obligatorio del XSD
        assert xml44.text(root, "Mensaje") == code
        assert ("MontoTotalImpuestoAcreditar" in names) is amounts, (code, cond)
        if cond == "05" or code == "3":
            assert "CodigoActividad" not in names
        fecha = xml44.text(root, "FechaEmisionDoc")
        assert fecha == "2026-10-14T23:30:00-06:00"  # hora de Costa Rica, sin microsegundos
        signed = sign_simulated(root)
        assert xsd.validate("mensaje_receptor", signed) == [], (code, cond, xsd.validate("mensaje_receptor", signed))
    # aceptacion 01 con impuestos que no son IVA: el credito no es el total -> lleva los montos
    root = _mr("1", "01", tax=Decimal("15000"), iva=Decimal("13000"))
    assert xml44.text(root, "MontoTotalImpuestoAcreditar") == "13000.00000" and xml44.text(root, "MontoTotalImpuesto") == "15000.00000"
    # emisor alfanumerico pasa el XSD (maxLength 20, sin patron)
    assert xsd.validate("mensaje_receptor", sign_simulated(_mr(emisor="3101AB1234"))) == []
    with pytest.raises(xml44.BuildError):
        _mr("1", "01", detail="abc")  # el anexo exige minimo 5 siempre que venga


def _seed_invoice(client, doc_type="FE"):
    cust = client.get("/customers").json()["items"][0]
    prods = client.get("/products", params={"q": "domo"}).json()["items"]
    serv = client.get("/products", params={"q": "Instalacion"}).json()["items"]
    lines = [{"product_id": prods[0]["id"], "quantity": 2}, {"product_id": serv[0]["id"], "quantity": 3, "unit": "Sp"}]
    body = {"customer_id": cust["id"] if doc_type != "TE" else None, "lines": lines, "discount_type": "percent", "discount_value": 5}
    r = client.post("/invoices", params={"doc_type": doc_type}, json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_fe_te_nc_de_la_semilla_validan(client, auth, db_session):
    from app.models import Invoice, Tenant
    from app.providers.einvoice import build_clave, cr_now
    from app.services.einvoice import direct_payload

    t = db_session.get(Tenant, auth["tenant"]["id"])
    inv = db_session.get(Invoice, _seed_invoice(client)["id"])
    fecha = cr_now()
    for doc_type, ref in (("FE", None), ("TE", None), ("NC", {"doc_type": "01", "number": clave(), "date": fecha, "code": "01", "reason": "Anula"})):
        cons = "00100001" + {"FE": "01", "TE": "04", "NC": "03"}[doc_type] + "0000000001"
        cl = build_clave("506", fecha, t.tax_id, cons, "1", "12345678")
        p = direct_payload(db_session, t, inv, doc_type, cons, cl, fecha, reference=ref)
        root = xml44.build_comprobante(p)
        errs = xsd.validate(doc_type, sign_simulated(root))
        assert errs == [], (doc_type, errs)
        assert [xml44.local(e) for e in root][:6] == ["Clave", "ProveedorSistemas", "CodigoActividadEmisor", "NumeroConsecutivo", "FechaEmision", "Emisor"]


def test_clave_con_fecha_de_costa_rica():
    from app.providers.einvoice import build_clave

    utc = datetime(2026, 10, 15, 5, 30, tzinfo=UTC)  # 23:30 del 14 en Costa Rica
    cl = build_clave("506", utc, "3-102-000000", "00100001010000000001", "1", "12345678")
    assert cl[3:9] == "141026" and len(cl) == 50


def test_tarifa_cero_sin_codigo_es_422(client, auth, db_session):
    from app.models import Invoice, Tenant
    from app.providers.einvoice import cr_now
    from app.services.einvoice import direct_payload

    cust = client.get("/customers").json()["items"][0]
    r = client.post("/invoices", json={"customer_id": cust["id"], "lines": [{"name": "Servicio exento", "cabys_code": "8121100000100", "quantity": 1, "unit_price": 1000, "tax_rate": 0}]})
    inv = db_session.get(Invoice, r.json()["id"])
    t = db_session.get(Tenant, auth["tenant"]["id"])
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        direct_payload(db_session, t, inv, "FE", "00100001010000000001", clave(), cr_now())
    assert e.value.status_code == 422
