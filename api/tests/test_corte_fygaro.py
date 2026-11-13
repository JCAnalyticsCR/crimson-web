"""Corte de Fygaro (Fygaro hasta el 31/10/2026, Crimson desde el 1/11/2026, Hacienda en Produccion): los avisos que
mandan a "confirmar en Fygaro" solo salen para comprobantes de antes del corte. Repo publico: datos ficticios."""

from datetime import UTC, date, datetime

from app.models import ReceivedDocument, ReceptionResponse, Tenant
from app.services import hacienda_tasks, reports
from app.services.corte_fygaro import CORTE, antes_del_corte

HOY = date(2026, 11, 25)


def test_antes_del_corte():
    assert CORTE == date(2026, 11, 1)
    assert antes_del_corte(date(2026, 10, 31)) and not antes_del_corte(date(2026, 11, 1)) and not antes_del_corte(None)


def _respondida_solo_en_pruebas(db_session, tid, n, emitida, vence):
    d = ReceivedDocument(
        tenant_id=tid,
        clave=str(n) * 50,
        doc_type="FE",
        issuer_name=f"Proveedor ficticio {n}",
        issuer_id="3101000000",
        issue_date=emitida,
        currency="CRC",
        total=1130,
        tax_total=130,
        status="respondido",
        action="aceptada",
        iva_condition="credito",
        deadline=vence,
        supplier_hacienda_status="aceptado",
    )
    db_session.add(d)
    db_session.flush()
    db_session.add(
        ReceptionResponse(
            tenant_id=tid,
            received_document_id=d.id,
            attempt=1,
            message_code="1",
            consecutive=f"0010000105{n:010d}",
            mode="pruebas",
            status="rechazado",
            emitted_at=datetime(2026, 11, 3, 15, 0, tzinfo=UTC),
        )
    )
    db_session.commit()
    return d


def test_avisos_de_fygaro_solo_para_lo_de_antes_del_corte(client, auth, db_session):
    tid = auth["tenant"]["id"]
    vieja = _respondida_solo_en_pruebas(db_session, tid, 1, date(2026, 10, 30), date(2026, 11, 24))
    nueva = _respondida_solo_en_pruebas(db_session, tid, 2, date(2026, 11, 10), date(2026, 11, 24))
    alertas = hacienda_tasks.alerts_for(db_session, db_session.get(Tenant, tid), today=HOY)
    del_viejo = [a for a in alertas if vieja.issuer_name in a]
    del_nuevo = [a for a in alertas if nueva.issuer_name in a]
    assert len(del_viejo) == 1 and "consta en Fygaro" in del_viejo[0]
    assert len(del_nuevo) == 1 and "Fygaro" not in del_nuevo[0] and "no llegó a Hacienda" in del_nuevo[0]
    # ninguna alerta manda a responder en Fygaro (ya no existe)
    assert not any("respóndala en Fygaro" in a for a in alertas)

    # el panel tributario marca cada fila para que el portal decida el texto
    x = reports.iva_breakdown(db_session, tid, date(2026, 10, 1), date(2026, 11, 30))
    marcas = {r["id"]: r["antes_corte"] for r in x["sin_mensaje_en_plazo"] + x["falta_respuesta_produccion"]}
    assert marcas == {vieja.id: True, nueva.id: False}
