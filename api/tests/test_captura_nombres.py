"""Como pesca Crimson el identificador (la clave de 50 digitos) y empareja XML + PDF + respuesta de Hacienda con los
nombres de archivo reales de los proveedores (Gosocket, Comprobante_/Acuse_Electronico_, MFS, Delta), por que un correo
quedo sin comprobantes, y que la clave sea coherente con su propio XML. Datos 100 % ficticios, montos de 1 130 colones."""

from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.services.reception_intake import name_keys
from tests.hacienda_utils import EMPRESA, PDF, clave, email_with, fe_xml, mensaje_hacienda

HOY = date.today()
FECHA = f"{HOY.isoformat()}T10:00:00-06:00"
DDMMAA = HOY.strftime("%d%m%y")
CED_A = "3101999001"  # proveedor ficticio A (estilo Gosocket)
CED_B = "3101999002"  # proveedor ficticio B (estilo Comprobante_Electronico_)
CED_C = "3101999003"


def doc(ced: str, consec: str):
    k = clave(fecha=DDMMAA, emisor=ced.zfill(12), consecutivo=consec)
    xml = fe_xml(k, emisor=ced, fecha=FECHA, consecutivo=consec, total=1130, iva=130, nombre=f"Proveedor Ficticio {ced[-1]} SA").encode()
    mh = mensaje_hacienda(k, emisor=ced, receptor=EMPRESA).encode()
    return k, xml, mh


def tenant(db, auth):
    from app.models import Tenant

    return db.get(Tenant, auth["tenant"]["id"])


def run(db, t, raw):
    from app.services.inbox import process_raw

    rm, res = process_raw(db, t, raw, mailbox_id=None, uid=None, uidvalidity=None)
    db.commit()
    return rm, res


def get(db, k):
    from app.models import ReceivedDocument

    return db.query(ReceivedDocument).filter_by(clave=k).one_or_none()


def pdf_of(db, d) -> bytes | None:
    from app.models import Media

    m = db.get(Media, d.pdf_media_id) if d.pdf_media_id else None
    return bytes(m.data) if m else None


def test_name_keys():
    k = clave(fecha=DDMMAA, emisor="003101999001", consecutivo="00400007010000000777")
    assert name_keys(f"{k}_respuesta.xml").claves == {k}
    n = name_keys("Comprobante_Electronico_3101999002_00500007010000088161.PDF")
    assert n.pares == {("3101999002", "00500007010000088161")} and n.consecs == {"00500007010000088161"} and not n.claves
    assert name_keys("MFS00100001010000000901.pdf").consecs == {"00100001010000000901"}
    assert name_keys("06600019401000007772.pdf").consecs == {"06600019401000007772"}
    assert not name_keys("promocion-noviembre.pdf") and not name_keys("factura (1).pdf")
    # cedula juridica alfanumerica (desde nov-2026): el par la reconoce
    assert ("3A01999004", "00500007010000000161") in name_keys("Comprobante_Electronico_3A01999004_00500007010000000161.pdf").pares


# 1) Gosocket: <clave>.pdf, <clave>.xml, <clave>_respuesta.xml
def test_nombre_clave(client, auth, db_session):
    t = tenant(db_session, auth)
    k, xml, mh = doc(CED_A, "00400007010000000777")
    raw = email_with([(f"{k}.pdf", PDF, "pdf"), (f"{k}.xml", xml, "xml"), (f"{k}_respuesta.xml", mh, "xml")], subject=f"Identificador: {k[:44]} {k[44:]}")
    rm, _ = run(db_session, t, raw)
    d = get(db_session, k)
    assert d and d.clave == k and d.consecutive == k[21:41] and rm.status == "procesado"
    assert d.pdf_media_id and d.xml_supplier_response and d.supplier_hacienda_status == "aceptado"


# 2) Comprobante_Electronico_<ced>_<consec>.PDF/.xml + Acuse_Electronico_<ced>_<consec>.xml
def test_nombre_cedula_consecutivo(client, auth, db_session):
    t = tenant(db_session, auth)
    c = "00500007010000000161"
    k, xml, mh = doc(CED_B, c)
    raw = email_with([(f"Comprobante_Electronico_{CED_B}_{c}.PDF", PDF, "pdf"), (f"Comprobante_Electronico_{CED_B}_{c}.xml", xml, "xml"), (f"Acuse_Electronico_{CED_B}_{c}.xml", mh, "xml")])
    run(db_session, t, raw)
    d = get(db_session, k)
    assert d.pdf_media_id and d.xml_supplier_response


# 3) prefijo + consecutivo (MFS...) y 4) consecutivo solo (estilo Delta)
def test_prefijo_y_consecutivo_solo(client, auth, db_session):
    t = tenant(db_session, auth)
    c1, c2 = "00100001010000000901", "06600019401000000772"
    k1, x1, m1 = doc(CED_C, c1)
    k2, x2, m2 = doc(CED_A, c2)
    run(db_session, t, email_with([(f"MFS{c1}.pdf", PDF + b"1", "pdf"), (f"MFS{c1}.xml", x1, "xml"), (f"MFS{c1}_MH.xml", m1, "xml")]))
    run(db_session, t, email_with([(f"{c2}.pdf", PDF + b"2", "pdf"), (f"{c2}.xml", x2, "xml"), (f"{c2}_respuesta.xml", m2, "xml")]))
    assert get(db_session, k1).pdf_media_id and get(db_session, k1).xml_supplier_response
    assert get(db_session, k2).pdf_media_id and get(db_session, k2).xml_supplier_response


# 5) dos facturas en un mismo correo -> cada PDF a la suya
def test_dos_facturas_un_correo(client, auth, db_session):
    t = tenant(db_session, auth)
    ca, cb = "00500007010000000201", "00500007010000000202"
    ka, xa, ma = doc(CED_B, ca)
    kb, xb, mb = doc(CED_B, cb)
    raw = email_with(
        [
            (f"Comprobante_Electronico_{CED_B}_{cb}.PDF", PDF + b"b", "pdf"),
            (f"Comprobante_Electronico_{CED_B}_{ca}.PDF", PDF + b"a", "pdf"),
            (f"Comprobante_Electronico_{CED_B}_{ca}.xml", xa, "xml"),
            (f"Comprobante_Electronico_{CED_B}_{cb}.xml", xb, "xml"),
            (f"Acuse_Electronico_{CED_B}_{ca}.xml", ma, "xml"),
            (f"Acuse_Electronico_{CED_B}_{cb}.xml", mb, "xml"),
        ]
    )
    run(db_session, t, raw)
    da, dbb = get(db_session, ka), get(db_session, kb)
    assert pdf_of(db_session, da).endswith(b"a") and pdf_of(db_session, dbb).endswith(b"b")
    assert da.xml_supplier_response and dbb.xml_supplier_response


# 6) dos facturas con PDF de nombre generico -> quedan sin asignar (no se adivina)
def test_dos_facturas_pdf_generico(client, auth, db_session):
    t = tenant(db_session, auth)
    _, xa, _ = doc(CED_A, "00100001010000000301")
    _, xb, _ = doc(CED_A, "00100001010000000302")
    _, res = run(db_session, t, email_with([("factura.pdf", PDF + b"x", "pdf"), ("factura (1).pdf", PDF + b"y", "pdf"), ("a.xml", xa, "xml"), ("b.xml", xb, "xml")]))
    assert len(res["sin_asignar"]) == 2


# 7) la respuesta de Hacienda llega en un correo POSTERIOR -> se amarra por la clave
def test_respuesta_despues(client, auth, db_session):
    t = tenant(db_session, auth)
    k, xml, mh = doc(CED_A, "00100001010000000401")
    run(db_session, t, email_with([(f"{k}.xml", xml, "xml"), (f"{k}.pdf", PDF, "pdf")]))
    assert not get(db_session, k).xml_supplier_response
    run(db_session, t, email_with([(f"{k}_respuesta.xml", mh, "xml")], subject="Respuesta de Hacienda"))
    assert get(db_session, k).xml_supplier_response


# 9) PDF suelto en un correo posterior; dos proveedores con el MISMO consecutivo: la cedula del nombre desempata
def test_pdf_suelto_la_cedula_desempata(client, auth, db_session):
    from app.models import AuditLog

    t = tenant(db_session, auth)
    c = "00100001010000000601"
    kb, xb, _ = doc(CED_B, c)
    kc, xc, _ = doc(CED_C, c)
    run(db_session, t, email_with([("x.xml", xb, "xml")]))
    run(db_session, t, email_with([("y.xml", xc, "xml")]))
    _, res = run(db_session, t, email_with([(f"Comprobante_Electronico_{CED_B}_{c}.PDF", PDF + b"z", "pdf")]))
    assert not res["sin_asignar"]
    assert get(db_session, kb).pdf_media_id and not get(db_session, kc).pdf_media_id
    a = db_session.query(AuditLog).filter(AuditLog.action == "reception_pdf_matched_db").one()
    assert a.entity_id == get(db_session, kb).id


# 10) una factura + un PDF de publicidad ADELANTE -> gana el PDF real
def test_pdf_publicidad_pierde_contra_el_real(client, auth, db_session):
    t = tenant(db_session, auth)
    c = "00500007010000000701"
    k, xml, _ = doc(CED_B, c)
    raw = email_with([("promocion-noviembre.pdf", PDF + b"promo", "pdf"), (f"Comprobante_Electronico_{CED_B}_{c}.PDF", PDF + b"real", "pdf"), (f"Comprobante_Electronico_{CED_B}_{c}.xml", xml, "xml")])
    _, res = run(db_session, t, raw)
    assert pdf_of(db_session, get(db_session, k)).endswith(b"real")
    assert [x["name"] for x in res["sin_asignar"]] == ["promocion-noviembre.pdf"]


# 11) correo nuevo con su factura + el PDF de una factura VIEJA -> el viejo va a la vieja
def test_pdf_de_otra_factura_va_a_la_suya(client, auth, db_session):
    t = tenant(db_session, auth)
    cv, cn = "00500007010000000801", "00500007010000000802"
    kv, xv, _ = doc(CED_B, cv)
    kn, xn, _ = doc(CED_B, cn)
    run(db_session, t, email_with([("vieja.xml", xv, "xml")]))
    run(db_session, t, email_with([(f"Comprobante_Electronico_{CED_B}_{cv}.PDF", PDF + b"vieja", "pdf"), (f"Comprobante_Electronico_{CED_B}_{cn}.xml", xn, "xml")]))
    assert pdf_of(db_session, get(db_session, kv)).endswith(b"vieja") and not get(db_session, kn).pdf_media_id


# 12) correo sin adjuntos con la clave en el cuerpo y un enlace (estilo HIOPOS) -> solo_enlace con el motivo
def test_solo_enlace_con_la_clave_en_el_cuerpo(client, auth, db_session):
    t = tenant(db_session, auth)
    k, _, _ = doc(CED_C, "00100001040000000999")
    msg = MIMEMultipart()
    msg["Subject"], msg["From"] = "Tiquet 123", "tiquets@restaurante.example"
    msg.attach(MIMEText(f"Su comprobante: https://descargas.example/x\nIdentificador: {k[:44]} {k[44:]}", "plain"))
    rm, _ = run(db_session, t, msg.as_bytes())
    assert rm.status == "solo_enlace" and rm.summary[0]["clave"] == k and rm.raw_eml is None
    assert "Su comprobante" not in str(rm.summary)  # no se guarda el cuerpo ni la URL
    out = client.get("/reception/messages").json()
    it = next(x for x in out["items"] if x["id"] == rm.id)
    assert "enlace" in it["motivo"] and "Cargar archivos" in it["motivo"]


def test_identificador_en_html_con_wbr_y_nbsp(client, auth, db_session):
    t = tenant(db_session, auth)
    k, _, _ = doc(CED_A, "00400007010000000778")
    html = f"<p>Identificador:&nbsp;<span>{k[:30]}<wbr>{k[30:44]}</span>&nbsp;{k[44:]}</p><a href='https://gosocket.example/x'>Ver</a>"
    msg = MIMEMultipart()
    msg["Subject"], msg["From"] = "Factura", "no-reply@gosocket.example"
    msg.attach(MIMEText(html, "html"))
    rm, _ = run(db_session, t, msg.as_bytes())
    assert rm.status == "solo_enlace" and rm.summary[0]["clave"] == k


def test_adjunto_que_no_es_factura_y_logo_inline(client, auth, db_session):
    from email.mime.image import MIMEImage

    t = tenant(db_session, auth)
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    msg = MIMEMultipart()
    msg["Subject"], msg["From"] = "Promoción", "promo@tienda.example"
    msg.attach(MIMEText("<img src='cid:logo'> Ofertas", "html"))
    logo = MIMEImage(png, _subtype="png")
    logo["Content-ID"], logo["Content-Disposition"] = "<logo>", "inline"
    msg.attach(logo)
    rm, _ = run(db_session, t, msg.as_bytes())
    assert rm.status == "sin_comprobantes" and not any(x["result"] == "no_fiscal" for x in rm.summary)
    foto = MIMEImage(png, _subtype="jpeg")
    foto["Content-Disposition"] = 'attachment; filename="recibo.jpg"'
    msg2 = MIMEMultipart()
    msg2["Subject"], msg2["From"] = "Recibo", "app@capcut.example"
    msg2.attach(foto)
    rm2, _ = run(db_session, t, msg2.as_bytes())
    assert rm2.status == "sin_comprobantes" and rm2.summary[0]["result"] == "no_fiscal" and rm2.summary[0]["name"] == "recibo.jpg"
    out = client.get("/reception/messages?all=1").json()
    it = next(x for x in out["items"] if x["id"] == rm2.id)
    assert it["motivo"] == "Los adjuntos no son XML, PDF ni zip"


# 14) dedupe: la misma factura en dos correos; el segundo trae el PDF que faltaba
def test_dedupe_y_pdf_en_el_reenvio(client, auth, db_session):
    t = tenant(db_session, auth)
    k, xml, _ = doc(CED_A, "00100001010000001001")
    run(db_session, t, email_with([(f"{k}.xml", xml, "xml")]))
    _, res = run(db_session, t, email_with([(f"{k}.xml", xml, "xml"), (f"{k}.pdf", PDF, "pdf")]))
    assert res["duplicados"] and get(db_session, k).pdf_media_id


# ---------------------------------------------------------------- coherencia de la clave (xml44)
def test_clave_coherente_sin_avisos_y_alterada_con_aviso(client, auth, db_session):
    from app.providers.hacienda import xml44

    k, xml, _ = doc(CED_A, "00100001010000001101")
    p = xml44.parse_comprobante(xml44.parse_xml_bytes(xml))
    assert not [w for w in p.warnings if "clave" in w]
    otro = fe_xml(k, emisor=CED_A, fecha=FECHA, consecutivo="00100001010000009999", total=1130, iva=130).encode()
    p = xml44.parse_comprobante(xml44.parse_xml_bytes(otro))
    assert "El consecutivo del XML no coincide con el de la clave" in p.warnings
    r = client.post("/reception/upload", files={"file": ("f.xml", otro, "application/xml")})
    assert r.status_code == 201  # solo avisa: se registra igual
    assert "El consecutivo del XML no coincide con el de la clave" in client.get(f"/reception/{r.json()['id']}").json()["warnings"]
    ajena = fe_xml(k, emisor=CED_B, fecha=FECHA, consecutivo="00100001010000001101", total=1130, iva=130).encode()
    assert "La cédula del emisor no coincide con la de la clave" in xml44.parse_comprobante(xml44.parse_xml_bytes(ajena)).warnings


# 05/11 (revision): Delta nombra el PDF con un numero de 20 digitos que NO es su consecutivo ni su clave. Con una sola
# factura y un solo PDF en el correo, el PDF se le pega igual (como antes) y queda constancia en la bitacora.
def test_unico_pdf_con_numero_ajeno_se_pega_a_la_unica_factura(client, auth, db_session):
    t = tenant(db_session, auth)
    cons = "00100001010000000903"
    k, x, m = doc(CED_A, cons)
    raw_name = "06600019401000007772"  # no es el consecutivo de la factura
    run(db_session, t, email_with([(f"{raw_name}.pdf", PDF + b"d", "pdf"), (f"{raw_name}.xml", x, "xml"), (f"{raw_name}_respuesta.xml", m, "xml")]))
    d = get(db_session, k)
    assert d.pdf_media_id and d.xml_supplier_response
