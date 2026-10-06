"""Auditoria del portal (5 oct 2026): pagos sin factura, conteos de soporte distintos entre inicio y lista,
margen real 100 % en proyectos sin costos."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models import Project, SupportTicket


def test_pagos_traen_factura_cliente_y_referencia(client, auth):
    cust = client.get("/customers").json()["items"][0]
    inv = client.post("/invoices", json={"customer_id": cust["id"], "lines": [{"name": "X", "quantity": 1, "unit_price": 100, "tax_rate": 13}]}).json()
    r = client.post(f"/invoices/{inv['id']}/payments", json={"method": "sinpe", "kind": "captura", "amount": "50", "external_ref": "SINPE-9"})
    assert r.status_code == 201, r.text
    pagos = client.get("/payments").json()
    p = next(x for x in pagos if x["external_ref"] == "SINPE-9")
    assert p["invoice_id"] == inv["id"] and p["invoice_number"] == inv["number"] and p["customer"] == cust["name"]
    # el inicio y la lista de pagos apuntan a la misma factura
    rec = client.get("/dashboard").json()["pagos_recientes"]
    assert next(x for x in rec if x["id"] == p["id"])["invoice_id"] == p["invoice_id"]


def test_soporte_inicio_y_lista_coinciden(client, auth, db_session):
    custs = client.get("/customers").json()["items"]
    ids = [client.post("/tickets", json={"subject": f"Caso {i}", "customer_id": custs[0]["id"], "priority": "alta"}).json()["id"] for i in range(5)]
    hace = datetime.now(UTC) - timedelta(days=3)
    # 0: respuesta vencida; 1: respondido pero resolucion vencida; 2: al dia; 3: resuelto con plazos vencidos; 4: cerrado
    t0, t1, t2, t3, t4 = (db_session.get(SupportTicket, i) for i in ids)
    t0.due_at = hace
    t1.first_reply_at, t1.due_at, t1.resolve_due_at = hace, hace, hace
    t3.due_at, t3.resolve_due_at, t3.status = hace, hace, "resuelto"
    t4.status = "cerrado"
    db_session.commit()

    g = client.get("/dashboard").json()["gerencia"]
    meta = client.get("/tickets/meta/config").json()
    abiertos = client.get("/tickets", params={"status": "abiertos"}).json()
    tarde = client.get("/tickets", params={"pendiente": "fuera_de_tiempo"}).json()
    assert g["tickets_open"] == meta["abiertos"] == len(abiertos) == 3
    assert g["tickets_late"] == meta["fuera_de_tiempo"] == len(tarde) == 2
    assert {t["id"] for t in tarde} == {ids[0], ids[1]} and all(t["fuera_de_tiempo"] for t in tarde)
    assert client.get("/tickets", params={"pendiente": "otro"}).status_code == 422


def test_proyecto_sin_costos_es_provisional(client, auth, db_session):
    cust = client.get("/customers").json()["items"][0]
    pr = client.post("/projects", json={"name": "Sin costos", "customer_id": cust["id"]}).json()
    obj = db_session.get(Project, pr["id"])
    obj.price, obj.cost_planned = Decimal("1000"), Decimal("700")
    db_session.commit()
    eco = client.get(f"/projects/{pr['id']}").json()["economics"]
    assert eco["sin_costos"] and eco["provisional"] and eco["margin_real"] is None and eco["profit"] is None
    assert eco["aviso"] == "Faltan costos por registrar" and round(eco["margin_planned"]) == 30

    # terminado sin costos: en reportes queda marcado y fuera de la utilidad
    client.put(f"/projects/{pr['id']}", json={"name": "Sin costos", "status": "terminado", "customer_id": cust["id"]})
    rent = client.get("/reports/rentabilidad").json()
    fila = next(r for r in rent["rows"] if r[0] == pr["number"])
    assert fila[-1] == "Faltan costos por registrar" and fila[11] is None
    assert Decimal(str(rent["totals"]["Utilidad"])) == 0 and rent["totals"]["Provisionales sin costos (fuera del total)"] == 1
    tipos = client.get("/reports/rentabilidad_tipo").json()
    assert tipos["rows"] == [] and tipos["totals"]["Provisionales sin costos (fuera del total)"] == 1
    assert client.get("/dashboard").json()["gerencia"]["profit_month"] in ("0.00", 0, "0")

    # con costos y cerrado: deja de ser provisional
    client.put(f"/projects/{pr['id']}", json={"name": "Sin costos", "status": "terminado", "cost_labor": 400, "customer_id": cust["id"]})
    eco = client.get(f"/projects/{pr['id']}").json()["economics"]
    assert not eco["provisional"] and round(eco["margin_real"]) == 60 and eco["aviso"] is None
