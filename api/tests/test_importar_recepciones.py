"""Importar el historico de recepciones de Fygaro (zip o CSV): vista previa sin escribir, historicas con los datos del
mensaje de Fygaro, gana Fygaro sobre lo respondido aqui en simulado, NO toca los consecutivos F05/F06/F07."""

import io
import zipfile
from datetime import date

from tests.hacienda_utils import EMPRESA, PDF, clave, fe_xml, mensaje_hacienda, mensaje_receptor, role_headers

FECHA = "2026-09-10T10:00:00-06:00"


def cl(n):
    return clave(fecha="100926", consecutivo=f"0010000101{n:010d}")


def zip_fygaro(items: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in items:
            z.writestr(n, b)
    return buf.getvalue()


def paquete(n: int, consecutivo_mr: str = "00100001050000000359", mensaje: str = "1") -> list[tuple[str, bytes]]:
    c = cl(n)
    return [
        (f"{c}.xml", fe_xml(c, fecha=FECHA, consecutivo=f"0010000101{n:010d}").encode()),
        (f"{c}-respuesta.xml", mensaje_hacienda(c).encode()),
        (f"{c}-mr.xml", mensaje_receptor(c, emisor="3101000001", receptor=EMPRESA, mensaje=mensaje, consecutivo=consecutivo_mr, detalle=None if mensaje == "1" else "Faltante").encode()),
        (f"{c}-mr-respuesta.xml", mensaje_hacienda(c, detalle="Confirmación de mensaje receptor aceptada").encode()),
        (f"{c}.pdf", PDF + str(n).encode()),
    ]


def importar(client, data: bytes, name="fygaro.zip", commit=False, headers=None, **params):
    return client.post("/import/recepciones", params={"commit": commit, **params}, files={"file": (name, data, "application/octet-stream")}, headers=headers)


def test_zip_vista_previa_y_aplicar_sin_tocar_consecutivos(client, auth, db_session):
    from app.models import BillingGroup, ReceivedDocument, ReceptionResponse

    data = zip_fygaro(paquete(1) + paquete(2, "00100001050000000360"))
    r = importar(client, data)
    assert r.status_code == 200, r.text
    prev = r.json()
    assert prev["summary"]["nuevo"] == 2 and not prev["commit"]
    assert any("360" in n for n in prev["notes"])
    assert db_session.query(ReceivedDocument).count() == 0  # la vista previa no escribe
    r = importar(client, data, commit=True).json()
    assert r["summary"]["nuevo"] == 2
    docs = db_session.query(ReceivedDocument).order_by(ReceivedDocument.clave).all()
    assert all(d.status == "historico" and d.origin == "historico" and d.action == "aceptada" and d.iva_condition == "credito" for d in docs)
    assert all(d.pdf_media_id and d.xml_supplier_response and d.activity_code == "6202.0" for d in docs)
    rs = db_session.query(ReceptionResponse).order_by(ReceptionResponse.consecutive).all()
    assert [x.consecutive for x in rs] == ["00100001050000000359", "00100001050000000360"] and all(x.status == "historico" and x.mode == "produccion" for x in rs)
    assert all(x.hacienda_xml and "Confirmación" in x.hacienda_detail for x in rs)
    f05 = db_session.query(BillingGroup).filter(BillingGroup.doc_type == "F05").one()
    assert f05.current == 0  # el importador NO sube el consecutivo
    st = client.get("/settings/hacienda").json()
    assert next(c for c in st["consecutives"] if c["code"] == "05")["fygaro_max"] == 360
    assert not next(c for c in st["production"]["checks"] if c["key"] == "fygaro")["ok"]  # rojo hasta que el admin lo suba
    # no se envian ni se reabren
    rid = docs[0].id
    det = client.get(f"/reception/{rid}").json()
    assert not det["can_respond"] and not det["can_reopen"]
    assert client.post(f"/reception/{rid}/respond", json={"action": "aceptada"}).status_code == 409
    # duplicado: omitir
    r = importar(client, data, commit=True).json()
    assert r["summary"]["omitir"] == 2


def test_gana_fygaro_sobre_lo_respondido_en_simulado(client, auth, db_session, monkeypatch):
    from app.models import ReceivedDocument
    from app.services import hacienda_config

    c = cl(3)
    d = client.post("/reception/upload", files={"file": ("f.xml", fe_xml(c, fecha=FECHA, consecutivo="00100001010000000003").encode(), "application/xml")}).json()
    client.post(f"/reception/{d['id']}/respond", json={"action": "aceptada", "iva_condition": "credito"})
    r = importar(client, zip_fygaro(paquete(3, mensaje="2")), commit=True).json()
    assert r["summary"]["actualizar"] == 1, r
    doc = db_session.get(ReceivedDocument, d["id"])
    db_session.refresh(doc)
    assert doc.status == "historico" and doc.action == "parcial"
    det = client.get(f"/reception/{d['id']}").json()
    assert [x["status"] for x in det["responses"]] == ["simulado", "historico"]
    monkeypatch.setattr(hacienda_config, "reception_mode", lambda db, t: "produccion")
    assert client.post(f"/reception/{d['id']}/reopen", json={"confirm_not_in_fygaro": True}).status_code == 409


def test_csv_con_sinonimos_y_gastos(client, auth, db_session):
    from app.models import Expense, ReceivedDocument

    c = cl(4)
    csv = (
        "Clave;Fecha;Tipo;Emisor;Cédula emisor;Moneda;Impuesto;Total;Acción;Condición IVA;Código actividad;Consecutivo receptor;Categoría\n"
        f"{c};10/09/2026;FE;Proveedor CSV SA;3101000001;CRC;13000;113000;Aceptado;01;6202.0;00100001050000000361;Servicios\n"
    ).encode()
    r = importar(client, csv, name="historico.csv", commit=True, create_expenses=True)
    assert r.status_code == 200 and r.json()["summary"]["nuevo"] == 1, r.text
    doc = db_session.query(ReceivedDocument).filter(ReceivedDocument.clave == c).one()
    assert doc.status == "historico" and doc.xml_document is None and doc.issuer_id == "3101000001"
    e = db_session.query(Expense).filter(Expense.received_document_id == doc.id).one()
    assert e.category_id is not None and float(e.total) == 113000
    assert client.get(f"/reception/{doc.id}/file/document").status_code == 404  # sin XML


def test_limites_y_permisos(client, auth, db_session):
    bomba = io.BytesIO()
    with zipfile.ZipFile(bomba, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("x.xml", b"<x>" + b"0" * (11 * 1024 * 1024) + b"</x>")
    assert importar(client, bomba.getvalue()).status_code == 422
    ventas = role_headers(client, db_session, auth["tenant"]["id"], "ventas.imp@ejemplo.com", "ventas")
    assert importar(client, zip_fygaro(paquete(5)), headers=ventas).status_code == 403
    conta = role_headers(client, db_session, auth["tenant"]["id"], "conta.imp@ejemplo.com", "contabilidad")
    assert importar(client, zip_fygaro(paquete(5)), headers=conta).status_code == 200
    assert client.get("/import/recepciones/template").status_code == 200
    assert date.today()


def test_consecutivo_de_simulado_no_choca_con_fygaro_y_un_choque_no_tumba_la_importacion(client, auth, db_session, monkeypatch):
    """T05 copia sucursal y terminal de F05: T05 n.o 1 es el mismo texto que la MR n.o 1 de Fygaro. El unico es por modo.
    En produccion el choque es real (F05 sin ajustar): 409 que lo explica. En la importacion, error de ESA fila."""
    from app.models import ReceivedDocument
    from app.services import hacienda_config

    hoy = date.today()
    r = importar(client, zip_fygaro(paquete(11, "00100001050000000001")), commit=True).json()
    assert r["summary"]["nuevo"] == 1

    def subir(n: int) -> int:
        c = clave(fecha=hoy.strftime("%d%m%y"), consecutivo=f"0010000101{n:010d}")
        xml = fe_xml(c, fecha=f"{hoy.isoformat()}T10:00:00-06:00", consecutivo=f"0010000101{n:010d}").encode()
        up = client.post("/reception/upload", files={"file": ("f.xml", xml, "application/xml")})
        assert up.status_code == 201, up.text
        return up.json()["id"]

    rid = subir(412)
    r = client.post(f"/reception/{rid}/respond", json={"action": "aceptada"})
    assert r.status_code == 200, r.text
    assert r.json()["responses"][0]["consecutive"] == "00100001050000000001" and r.json()["responses"][0]["mode"] == "simulado"
    monkeypatch.setattr(hacienda_config, "reception_mode", lambda db, t: "produccion")
    rid2 = subir(413)
    r = client.post(f"/reception/{rid2}/respond", json={"action": "aceptada", "send_now": False})
    assert r.status_code == 409 and "Consecutivos" in r.json()["detail"] and "otra persona" not in r.json()["detail"]
    # importar: la fila cuyo mensaje choca con uno de produccion ya registrado queda como error; las demas entran
    r = importar(client, zip_fygaro(paquete(12, "00100001050000000001") + paquete(13, "00100001050000000002")), commit=True)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["summary"]["error"] == 1 and res["summary"]["nuevo"] == 1, res["result"]
    assert db_session.query(ReceivedDocument).filter(ReceivedDocument.clave == cl(13)).count() == 1
    assert db_session.query(ReceivedDocument).filter(ReceivedDocument.clave == cl(12)).count() == 0


def test_gana_fygaro_cierra_el_mensaje_de_pruebas_pendiente(client, auth, db_session, monkeypatch, p12_empresa):
    """Dia del cambio (§15.7b): un mensaje de pruebas aun por enviar o en consulta cuando se importa el de Fygaro se cierra.
    El worker no lo manda ni lo consulta, la recepcion sigue "historico" y no da alertas."""
    from app.models import ReceivedDocument, ReceptionResponse, Tenant
    from app.services import hacienda_tasks
    from app.services.hacienda_sync import poll_status, send_pending
    from tests.hacienda_utils import FakeHacienda, modo_pruebas

    fake = FakeHacienda(sequence=["procesando"]).install(monkeypatch)
    modo_pruebas(client, *p12_empresa)
    ids = []
    for n, enviar in ((21, False), (22, True)):
        xml = fe_xml(cl(n), fecha=FECHA, consecutivo=f"0010000101{n:010d}").encode()
        d = client.post("/reception/upload", files={"file": ("f.xml", xml, "application/xml")}).json()
        r = client.post(f"/reception/{d['id']}/respond", json={"action": "aceptada", "iva_condition": "credito", "send_now": enviar}).json()
        assert r["hacienda_status"] == ("enviado" if enviar else "por_enviar"), r
        ids.append(d["id"])
    pruebas = db_session.query(ReceptionResponse).filter(ReceptionResponse.mode == "pruebas").all()
    for x in pruebas:
        x.next_attempt_at = None  # sin el cierre, el worker los tomaria en esta misma vuelta
    db_session.commit()
    posts, gets = len(fake.posts), len(fake.gets)
    r = importar(client, zip_fygaro(paquete(21) + paquete(22, "00100001050000000360")), commit=True).json()
    assert r["summary"]["actualizar"] == 2, r
    for x in pruebas:
        db_session.refresh(x)
        assert x.status == "error" and "Fygaro" in x.last_error and x.next_attempt_at is None
    send_pending(db_session)
    poll_status(db_session)
    assert len(fake.posts) == posts and len(fake.gets) == gets
    for rid in ids:
        doc = db_session.get(ReceivedDocument, rid)
        db_session.refresh(doc)
        assert doc.status == "historico" and doc.hacienda_status == "historico"
        det = client.get(f"/reception/{rid}").json()
        assert [x["status"] for x in det["responses"]] == ["error", "historico"] and not det["can_retry"] and not det["can_send"]
    t = db_session.get(Tenant, auth["tenant"]["id"])
    assert not [a for a in hacienda_tasks.alerts_for(db_session, t) if "ensaje receptor" in a]
