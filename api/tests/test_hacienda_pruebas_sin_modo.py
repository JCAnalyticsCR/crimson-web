"""Hacienda de pruebas sin tocar las ventas (plan maestro 1.11): hacienda_smoke --sin-modo usa las credenciales de
PRUEBAS con la empresa en "ninguno" y no cambia el modo, no crea facturas ni gasta la numeracion visible. Pasar la
empresa a Pruebas desde Ajustes pide escribir PRUEBAS: cada venta real se emitiria al ambiente de pruebas."""

from tests.hacienda_utils import FakeHacienda, load_credentials


def test_cuando_corre_la_prueba():
    from app.tools.hacienda_smoke import mode_problem

    assert mode_problem("pruebas", False, True) is None
    assert "--sin-modo" in mode_problem("ninguno", False, True)  # sin el flag, como antes: exige modo Pruebas
    assert mode_problem("ninguno", True, True) is None
    assert mode_problem("simulado", True, True) is None
    assert "Producción" in mode_problem("produccion", True, True)  # con produccion activa no se corre
    assert "--confirmo-pruebas" in mode_problem("ninguno", True, False)


def test_smoke_sin_modo_no_toca_ventas_ni_modo(client, auth, db_session, monkeypatch, p12_empresa):
    from app.models import BillingGroup, Invoice, Tenant
    from app.providers.hacienda.provider import HaciendaDirectProvider
    from app.services import hacienda_config as hc
    from app.tools.hacienda_smoke import smoke

    fake = FakeHacienda(sequence=["aceptado"]).install(monkeypatch)
    assert load_credentials(client, *p12_empresa).status_code == 200  # Ajustes -> Hacienda -> Credenciales -> Pruebas
    t = db_session.get(Tenant, auth["tenant"]["id"])
    assert hc.hacienda_mode(db_session, t) == "ninguno"
    settings_antes = dict(t.settings or {})
    grupos = lambda: {g.doc_type: g.current for g in db_session.query(BillingGroup).filter(BillingGroup.tenant_id == t.id)}  # noqa: E731
    antes = grupos()
    facturas = db_session.query(Invoice).count()

    creds, err = hc.credentials_status(db_session, t, "pruebas")
    assert creds and not err

    def esperar(prov, ref, label):
        return prov.get_status(ref).kind

    assert smoke(db_session, t, "4321000000000", HaciendaDirectProvider(t, "pruebas", creds), wait=esperar) == 0
    # FE de prueba y mensaje receptor aceptados en el ambiente de PRUEBAS (nunca produccion)
    assert len(fake.posts) == 2 and not fake.production_hits and fake.tokens
    db_session.refresh(t)
    assert hc.hacienda_mode(db_session, t) == "ninguno" and (t.settings or {}) == settings_antes  # el modo no cambio
    assert db_session.query(Invoice).count() == facturas  # ninguna factura nueva ni tocada
    despues = grupos()
    cambiaron = {k for k in despues if despues[k] != antes.get(k)}
    assert cambiaron == {"T01", "T05"}  # solo la numeracion de pruebas; FEC, TE y F01..F07 intactas


def test_modo_pruebas_pide_confirmacion(client, auth):
    r = client.put("/settings/hacienda/mode", json={"mode": "pruebas"})
    assert r.status_code == 422 and "PRUEBAS" in r.json()["detail"] and "no se podría emitir en producción" in r.json()["detail"]
    assert client.get("/settings/hacienda").json()["mode"] == "ninguno"
    assert client.put("/settings/hacienda/mode", json={"mode": "pruebas", "confirm_text": "SI"}).status_code == 422
    r = client.put("/settings/hacienda/mode", json={"mode": "pruebas", "confirm_text": " PRUEBAS "})
    assert r.status_code == 200 and r.json()["mode"] == "pruebas"
    assert client.put("/settings/hacienda/mode", json={"mode": "pruebas"}).status_code == 200  # ya estaba: no cambia nada
    assert client.put("/settings/hacienda/mode", json={"mode": "none"}).json()["mode"] == "ninguno"  # salir no pide nada
