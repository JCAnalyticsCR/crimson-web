"""Sesion 4: multimedia, ficha de cliente, variantes, envio por peso, planillas, conciliacion, eventos con QR,
POS, importador, soporte auditado, bandeja IMAP, recurrencias de gasto, reportes nuevos y limite de login."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from tests.conftest import ADMIN

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000100e221bc330000000049454e44ae426082")


def _publish(client, kind="tienda"):
    return client.put("/store", json={"kind": kind, "published": True}).json()


def _product(client, q="PTZ"):
    return client.get("/products", params={"q": q}).json()["items"][0]


# ---------- multimedia ----------
def test_media_upload_serve_and_reject(client, auth):
    r = client.post("/media", files={"file": ("logo final.png", PNG, "image/png")})
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["content_type"] == "image/png" and "/api/media/f/" in m["url"]
    again = client.post("/media", files={"file": ("copia.png", PNG, "image/png")}).json()
    assert again["id"] == m["id"]  # misma imagen -> mismo registro
    f = client.get(f"/media/f/{m['key']}")
    assert f.status_code == 200 and f.content == PNG and f.headers["x-content-type-options"] == "nosniff"
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert client.post("/media", files={"file": ("x.png", svg, "image/png")}).status_code == 415
    assert client.get("/media").json()[0]["id"] == m["id"]


# ---------- ficha de cliente ----------
def test_customer_contacts_notes_and_timeline(client, auth):
    c = client.get("/customers").json()["items"][0]
    k = client.post(f"/customers/{c['id']}/contacts", json={"name": "Ana Admin", "role": "Contabilidad", "email": "ana@ejemplo.com", "receives_invoices": True})
    assert k.status_code == 201
    assert client.post(f"/customers/{c['id']}/notes", json={"body": "Pide factura a nombre de la junta"}).status_code == 201
    inv = client.post("/invoices", json={"customer_id": c["id"], "lines": [{"name": "Servicio", "quantity": 1, "unit_price": 10000, "tax_rate": 13}]}).json()
    client.post(f"/invoices/{inv['id']}/payments", json={"method": "sinpe", "amount": 5000})
    ov = client.get(f"/customers/{c['id']}/overview").json()
    kinds = {e["kind"] for e in ov["timeline"]}
    assert {"nota", "factura", "pago"} <= kinds
    assert Decimal(str(ov["kpis"]["due"])) == Decimal("6300")
    assert client.get(f"/customers/{c['id']}/contacts").json()[0]["receives_invoices"] is True


# ---------- variantes + link + envio por peso ----------
def test_variants_in_store_cart_and_weight_shipping(client, auth):
    _publish(client)
    cam = _product(client, "DOME")
    client.put(
        f"/products/{cam['id']}",
        json={
            **{k: cam[k] for k in ("name", "code", "item_type", "price", "currency", "cabys_code", "unit", "category_id")},
            "tax_ids": cam["tax_ids"],
            "show_on_web": True,
            "weight_kg": 2.5,
            "images": [],
        },
    )
    vs = client.put(
        f"/products/{cam['id']}/variants",
        json=[
            {"name": "Blanca", "code": "CAM-DOME-W", "options": {"Color": "Blanco"}},
            {"name": "Negra PRO", "code": "CAM-DOME-B", "price": 75000, "options": {"Color": "Negro"}},
        ],
    )
    assert vs.status_code == 200 and len(vs.json()) == 2
    dup = client.put(f"/products/{cam['id']}/variants", json=[{"name": "X", "code": "NVR-8CH"}])
    assert dup.status_code == 409  # choca con el codigo de otro producto
    black = next(v for v in vs.json() if v["code"] == "CAM-DOME-B")
    pub = client.get(f"/public/store/crimson/products/{cam['id']}").json()
    assert len(pub["variants"]) == 2
    link = client.get(f"/products/{cam['id']}/link").json()
    assert link["url"].endswith(f"/tienda/crimson?p={cam['id']}") and link["ready"]

    client.put("/store", json={"shipping_rates": [{"name": "Correos de CR", "amount": 2000, "per_kg": 1000, "overhead_pct": 10, "active": True}]})
    body = {
        "lines": [{"product_id": cam["id"], "variant_id": black["id"], "quantity": 2}],
        "contact": {"name": "Luis", "email": "luis@ejemplo.com"},
        "shipping_method": "Correos de CR",
    }
    q = client.post("/public/store/crimson/quote", json=body).json()
    # envio = (2000 + 1000 x 5 kg) x 1.10 = 7700
    assert Decimal(str(q["shipping"])) == Decimal("7700.00")
    o = client.post("/public/store/crimson/checkout", json=body)
    assert o.status_code == 201
    order = client.get("/orders").json()[0]
    assert order["lines"][0]["name"].endswith("Negra PRO") and Decimal(str(order["lines"][0]["unit_price"])) == Decimal("75000")


# ---------- planillas ----------
def test_payroll_run_approve_pay(client, auth):
    e1 = client.post("/payroll/employees", json={"name": "Técnico Uno", "salary": 800000, "frequency": "mensual"}).json()
    client.post("/payroll/employees", json={"name": "Jefa Dos", "salary": 1500000, "frequency": "mensual", "children": 1})
    run = client.post("/payroll/runs", json={"period_start": "2026-09-01", "period_end": "2026-09-30", "items": [{"employee_id": e1["id"], "overtime": 50000}]})
    assert run.status_code == 201, run.text
    r = run.json()
    one = next(x for x in r["lines"] if x["employee_name"] == "Técnico Uno")
    assert Decimal(str(one["gross"])) == Decimal("850000.00")
    assert Decimal(str(one["income_tax"])) == Decimal("0.00")  # bajo el tramo exento
    assert Decimal(str(one["ccss_worker"])) == Decimal("92055.00")  # 10.83 %
    two = next(x for x in r["lines"] if x["employee_name"] == "Jefa Dos")
    # (1 352 000-922 000) x 10 % + (1 500 000-1 352 000) x 15 % - 1 710 = 43 000 + 22 200 - 1 710
    assert Decimal(str(two["income_tax"])) == Decimal("63490.00")
    assert client.post(f"/payroll/runs/{r['id']}/pay", json={"date": "2026-09-30"}).status_code == 409  # sin aprobar
    assert client.post(f"/payroll/runs/{r['id']}/approve").json()["status"] == "aprobada"
    paid = client.post(f"/payroll/runs/{r['id']}/pay", json={"date": "2026-09-30"}).json()
    assert paid["status"] == "pagada" and paid["expense_id"]
    exp = next(x for x in client.get("/expenses").json() if x["id"] == paid["expense_id"])
    assert Decimal(str(exp["total"])) == Decimal(str(paid["employer_cost"]))
    slip = client.get(f"/payroll/runs/{r['id']}/slip/{one['id']}")
    assert slip.status_code == 200 and "Neto a pagar" in slip.text


# ---------- conciliacion ----------
def test_bank_reconciliation(client, auth):
    acct = client.post("/settings/bank-accounts", json={"name": "BAC colones", "bank": "BAC", "currency": "CRC"}).json()
    c = client.get("/customers").json()["items"][0]
    inv = client.post("/invoices", json={"customer_id": c["id"], "lines": [{"name": "Servicio", "quantity": 1, "unit_price": 100000, "tax_rate": 13}]}).json()
    client.post(f"/invoices/{inv['id']}/payments", json={"method": "transferencia", "amount": 113000, "external_ref": "TRF-889", "paid_at": "2026-09-10"})
    csv_data = (
        "Estado de cuenta BAC\n"
        "Fecha;Descripción;Referencia;Débito;Crédito\n"
        "11/09/2026;TRANSFERENCIA SINPE;TRF-889;;113.000,00\n"
        "12/09/2026;COMISION MANTENIMIENTO;;2.500,00;\n"
        "13/09/2026;DEPOSITO SIN IDENTIFICAR;;;50.000,00\n"
    ).encode()
    r = client.post(f"/banking/{acct['id']}/import", files={"file": ("bac.csv", csv_data, "text/csv")})
    assert r.status_code == 201, r.text
    assert r.json()["added"] == 3 and r.json()["matched"] == 1
    again = client.post(f"/banking/{acct['id']}/import", files={"file": ("bac.csv", csv_data, "text/csv")}).json()
    assert again["added"] == 0 and again["duplicates"] == 3
    lines = client.get(f"/banking/{acct['id']}/lines").json()
    fee = next(x for x in lines if x["description"].startswith("COMISION"))
    made = client.post(f"/banking/lines/{fee['id']}/expense", json={"description": "Comisión bancaria"})
    assert made.status_code == 201 and made.json()["status"] == "conciliado"
    unknown = next(x for x in lines if x["description"].startswith("DEPOSITO"))
    assert client.post(f"/banking/lines/{unknown['id']}/ignore").json()["status"] == "ignorado"
    summary = next(a for a in client.get("/banking/accounts").json() if a["id"] == acct["id"])
    assert summary["reconciled"] == 2 and summary["ignored"] == 1 and summary["pending"] == 0


# ---------- eventos ----------
def test_events_tickets_payment_and_checkin(client, auth):
    start = (datetime.now(UTC) + timedelta(days=10)).isoformat()
    ev = client.post(
        "/events",
        json={
            "name": "Expo Seguridad 2026",
            "starts_at": start,
            "venue": "Alajuela",
            "status": "publicado",
            "ticket_types": [{"name": "General", "price": 11300, "quantity": 3, "max_per_order": 2}],
        },
    )
    assert ev.status_code == 201, ev.text
    e = ev.json()
    tt = e["ticket_types"][0]
    buy = {"items": [{"ticket_type_id": tt["id"], "quantity": 2}], "name": "María", "email": "maria@ejemplo.com", "payment_method": "SINPE Móvil"}
    r = client.post(f"/public/events/crimson/{e['slug']}/checkout", json=buy)
    assert r.status_code == 201, r.text
    assert Decimal(str(r.json()["total"])) == Decimal("22600.00") and not r.json()["free"]
    assert client.post(f"/public/events/crimson/{e['slug']}/checkout", json=buy).status_code == 409  # quedan 1
    tickets = client.get(f"/events/{e['id']}/tickets").json()
    assert len(tickets) == 2 and all(t["status"] == "pendiente" for t in tickets)
    code = tickets[0]["code"]
    assert client.post("/events/checkin", json={"code": code}).json()["ok"] is False  # pago pendiente
    order = next(o for o in client.get("/orders").json() if o["channel"] == "evento")
    client.patch(f"/orders/{order['id']}", json={"status": "pagado"})
    pub = client.get(f"/public/tickets/{code}").json()
    assert pub["status"] == "valido" and pub["qr_svg"].startswith("<svg")
    ok = client.post("/events/checkin", json={"code": f"https://portal/entrada/{code}"}).json()
    assert ok["ok"] is True and ok["holder_name"] == "María"
    twice = client.post("/events/checkin", json={"code": code}).json()
    assert twice["ok"] is False and "Ya ingresó" in twice["reason"]
    court = client.post(f"/events/{e['id']}/tickets", json={"ticket_type_id": tt["id"], "holder_name": "Invitado"}).json()
    assert court[0]["status"] == "valido"
    assert client.get(f"/events/{e['id']}").json()["stats"] == {"valido": 2, "usado": 1, "pendiente": 0, "anulado": 0}


# ---------- POS ----------
def test_pos_sale_with_change_and_split(client, auth):
    cat = client.get("/pos/catalog").json()
    nvr = next(p for p in cat if p["code"] == "NVR-8CH")
    stock0 = Decimal(str(nvr["stock"]))
    r = client.post(
        "/pos/sale",
        json={
            "lines": [{"product_id": nvr["id"], "quantity": 1}],
            "payments": [{"method": "tarjeta", "amount": 100000}, {"method": "efectivo", "amount": 120000}],
        },
    )
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["doc_type"] == "TE" and Decimal(str(s["total"])) == Decimal("213570")
    assert Decimal(str(s["change"])) == Decimal("6430")
    inv = client.get(f"/invoices/{s['invoice_id']}").json()
    assert inv["status"] == "pagada" and Decimal(str(inv["balance"])) == 0
    assert Decimal(str(next(p for p in client.get("/pos/catalog").json() if p["id"] == nvr["id"])["stock"])) == stock0 - 1
    short = client.post("/pos/sale", json={"lines": [{"product_id": nvr["id"], "quantity": 1}], "payments": [{"method": "efectivo", "amount": 1000}]})
    assert short.status_code == 422
    assert (
        client.post(
            "/pos/sale", json={"doc_type": "FE", "lines": [{"product_id": nvr["id"], "quantity": 1}], "payments": [{"method": "efectivo", "amount": 999999}]}
        ).status_code
        == 422
    )


# ---------- importador ----------
def test_importer_preview_commit_and_history(client, auth):
    csv_customers = "Nombre,Identificación,Correo electrónico,Teléfono\nFerretería El Tornillo,3-101-999888,compras@tornillo.cr,+506 2222 0000\nFerretería El Tornillo,3-101-999888,compras@tornillo.cr,\n,3-101-000111,sin@nombre.cr,\n".encode()
    prev = client.post("/import/customers", files={"file": ("clientes.csv", csv_customers, "text/csv")}).json()
    assert prev["summary"] == {"nuevo": 1, "actualizar": 0, "omitir": 1, "error": 1} and prev["commit"] is False
    assert not client.get("/customers", params={"q": "Tornillo"}).json()["items"]
    client.post("/import/customers?commit=true", files={"file": ("clientes.csv", csv_customers, "text/csv")})
    c = client.get("/customers", params={"q": "Tornillo"}).json()["items"][0]
    assert c["id_type"] == "juridica"

    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Reporte de productos"])  # titulo arriba, como los exportes reales
    ws.append(["Código", "Nombre", "Precio", "IVA", "CABYS"])
    ws.append(["DVR-4", "DVR 4 canales", 55000, 0.13, "4223200000200"])
    ws.append(["CAM-DOME-4MP", "Camara domo 4MP (precio nuevo)", "72.500,00", "13%", None])
    ws.append(["BAD", "Malo", 1000, 7, None])
    buf = BytesIO()
    wb.save(buf)
    res = client.post("/import/products?commit=true", files={"file": ("productos.xlsx", buf.getvalue(), "application/octet-stream")}).json()
    assert res["summary"] == {"nuevo": 1, "actualizar": 1, "omitir": 0, "error": 1}
    dome = client.get("/products", params={"q": "CAM-DOME-4MP"}).json()["items"][0]
    assert Decimal(str(dome["price"])) == Decimal("72500")

    hist = "Numero,Fecha,Cliente,Identificacion,Subtotal,Impuesto,Total,Saldo\nFE-9001,15/08/2026,Ferretería El Tornillo,3-101-999888,100000,13000,113000,0\nFE-9002,20/08/2026,Cliente Nuevo SA,3-101-111222,50000,6500,56500,56500\n".encode()
    h = client.post("/import/invoices?commit=true", files={"file": ("facturas.csv", hist, "text/csv")}).json()
    assert h["summary"]["nuevo"] == 2
    ov = client.get(f"/customers/{c['id']}/overview").json()
    assert ov["kpis"]["invoices"] == 1 and Decimal(str(ov["kpis"]["due"])) == 0
    assert client.get("/import/customers/template").status_code == 200


# ---------- soporte auditado ----------
def test_support_grant_audited_readonly_and_revoke(client, auth):
    g = client.post("/settings/support", json={"email": "soporte.externo@ejemplo.com", "hours": 2, "reason": "Revisar cierre de caja"}).json()
    assert g["status"] == "activo" and g["invite_link"]
    token = g["invite_link"].rsplit("/", 1)[-1]
    assert client.post("/settings/invitations/accept", json={"token": token, "full_name": "Soporte JC", "password": "Soporte-2026-ok"}).status_code == 200
    admin_headers = dict(client.headers)
    client.headers.pop("Authorization", None)
    tok = client.post("/auth/login", json={"email": "soporte.externo@ejemplo.com", "password": "Soporte-2026-ok"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/invoices", headers=h).status_code == 200
    assert client.post("/customers", json={"name": "No"}, headers=h).status_code == 403
    client.headers.update(admin_headers)
    log = client.get(f"/settings/support/{g['id']}/log").json()
    assert any(x["action"] == "soporte GET" and x["entity"] == "/invoices" for x in log)
    client.post(f"/settings/support/{g['id']}/revoke")
    assert client.get("/invoices", headers=h).status_code in (401, 403)
    assert "soporte" not in client.get("/settings/users").json()["roles"]


# ---------- bandeja IMAP ----------
class FakeImap:
    def __init__(self, raw_messages):
        self.msgs = raw_messages
        self.seen = []

    def login(self, u, p):
        assert p == "clave-app-123"

    def select(self, folder):
        return "OK", [b"1"]

    def search(self, charset, crit):
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.msgs)))]

    def fetch(self, mid, what):
        return "OK", [(b"1 (RFC822)", self.msgs[int(mid) - 1])]

    def store(self, mid, flags, val):
        self.seen.append(mid)

    def logout(self):
        pass


def test_inbox_imap_imports_xml(client, auth, db_session):
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart

    from app.models import Tenant
    from app.services.inbox import poll

    clave = "50601092600310123456700100001010000000001100000001"
    xml = f"<FacturaElectronica><Clave>{clave}</Clave><NumeroConsecutivo>00100001010000000001</NumeroConsecutivo><FechaEmision>2026-09-01T10:00:00-06:00</FechaEmision><Emisor><Nombre>Distribuidora Tec</Nombre><Identificacion><Numero>3101234567</Numero></Identificacion></Emisor><ResumenFactura><CodigoMoneda>CRC</CodigoMoneda><TotalVentaNeta>100000</TotalVentaNeta><TotalImpuesto>13000</TotalImpuesto><TotalComprobante>113000</TotalComprobante></ResumenFactura></FacturaElectronica>"
    msg = MIMEMultipart()
    msg["Subject"] = "Factura electronica"
    msg.attach(MIMEApplication(xml.encode(), Name="factura.xml", _subtype="xml"))
    msg.get_payload()[0]["Content-Disposition"] = 'attachment; filename="factura.xml"'
    resp = MIMEMultipart()
    resp.attach(MIMEApplication(b"<MensajeHacienda><Clave>x</Clave></MensajeHacienda>", _subtype="xml"))
    resp.get_payload()[0]["Content-Disposition"] = 'attachment; filename="respuesta.xml"'
    assert client.put(
        "/settings/inbox", json={"enabled": True, "host": "imap.ejemplo.com", "user": "facturas@ejemplo.com", "password": "clave-app-123"}
    ).json()["has_password"]
    fake = FakeImap([msg.as_bytes(), resp.as_bytes(), msg.as_bytes()])
    t = db_session.get(Tenant, auth["tenant"]["id"])
    res = poll(db_session, t, connect=lambda: fake)
    assert res["nuevos"] == 1 and res["duplicados"] == 1 and res["ignorados"] == 1 and len(fake.seen) == 3
    assert client.get("/reception").json()[0]["clave"] == clave
    assert client.get("/settings/inbox").json()["last_result"]["nuevos"] == 1


# ---------- recurrencia de gasto + reportes nuevos + limite de login ----------
def test_expense_recurrence_reports_and_login_limit(client, auth):
    r = client.post(
        "/recurrences",
        json={
            "name": "Alquiler bodega",
            "kind": "gasto",
            "template": {"description": "Alquiler bodega Alajuela", "subtotal": 450000, "tax_rate": 13},
            "frequency": "mensual",
            "next_date": date.today().isoformat(),
        },
    )
    assert r.status_code == 201, r.text
    run = client.post(f"/recurrences/{r.json()['id']}/run").json()
    assert run["expense_id"] and Decimal(str(run["total"])) == Decimal("508500")
    assert (
        client.post("/recurrences", json={"name": "X", "kind": "factura", "template": {"lines": []}, "next_date": date.today().isoformat()}).status_code == 422
    )

    a, b = "2026-01-01", "2026-12-31"
    for key in ("ordenes", "recepciones", "propinas", "d151", "planilla", "conciliacion"):
        rep = client.get(f"/reports/{key}", params={"from": a, "to": b})
        assert rep.status_code == 200, (key, rep.text)
        assert client.get(f"/reports/{key}", params={"from": a, "to": b, "format": "xlsx"}).status_code == 200

    codes = [client.post("/auth/login", json={"email": "nadie@ejemplo.com", "password": "mala-mala-1"}).status_code for _ in range(21)]
    assert codes[-1] == 429 and set(codes[:19]) == {401}
    assert ADMIN  # el admin sigue existiendo; el limite es por IP, no bloquea la cuenta


def test_store_contact_form_and_partial_updates(client, auth):
    client.put("/store", json={"kind": "catalogo", "published": True})
    r = client.post("/public/store/crimson/contact", json={"name": "Carla Ruiz", "email": "carla@ejemplo.com", "message": "Necesito 8 cámaras para una bodega"})
    assert r.status_code == 201
    c = client.get("/customers", params={"q": "carla@ejemplo.com"}).json()["items"][0]
    tl = client.get(f"/customers/{c['id']}/overview").json()["timeline"]
    assert tl[0]["kind"] == "nota" and "8 cámaras" in tl[0]["title"]
    # editar sin enviar un campo no lo borra
    client.put(f"/customers/{c['id']}", json={"name": "Carla Ruiz", "address": {"senas": "Alajuela centro"}})
    client.put(f"/customers/{c['id']}", json={"name": "Carla Ruiz Mora"})
    ov = client.get(f"/customers/{c['id']}/overview").json()["customer"]
    assert ov["name"] == "Carla Ruiz Mora" and ov["address"]["senas"] == "Alajuela centro" and ov["email"] == "carla@ejemplo.com"
    prod = client.get("/products", params={"q": "PTZ"}).json()["items"][0]
    client.put(f"/products/{prod['id']}", json={"name": prod["name"], "code": prod["code"], "images": [{"url": "https://x/img.png", "main": True}]})
    client.put(f"/products/{prod['id']}", json={"name": "PTZ renombrada", "code": prod["code"]})
    again = client.get(f"/products/{prod['id']}").json()
    assert again["name"] == "PTZ renombrada" and again["images"] and again["tax_ids"] == prod["tax_ids"] and again["category_id"] == prod["category_id"]


def test_vendedor_role_scope_and_test_invites(client, auth, db_session):
    from app import bootstrap

    links = bootstrap.extra_invites(db_session, "vendedor.prueba@ejemplo.com:ventas, malo:rolx", base_url="https://portal.test")
    assert len(links) == 1 and links[0][1] == "ventas"
    assert bootstrap.extra_invites(db_session, "vendedor.prueba@ejemplo.com:ventas") == []  # no repite mientras la invitacion siga viva
    token = links[0][2].rsplit("/", 1)[-1]
    assert client.post("/settings/invitations/accept", json={"token": token, "full_name": "Vale Vendedora", "password": "Vendedora-2026-ok"}).status_code == 200
    # una venta del admin (no debe aparecer en el dashboard del vendedor)
    c = client.get("/customers").json()["items"][0]
    client.post("/invoices", json={"customer_id": c["id"], "lines": [{"name": "Venta admin", "quantity": 1, "unit_price": 500000, "tax_rate": 13}]})
    admin_h = dict(client.headers)
    client.headers.pop("Authorization", None)
    tok = client.post("/auth/login", json={"email": "vendedor.prueba@ejemplo.com", "password": "Vendedora-2026-ok"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    me = client.get("/auth/me", headers=h).json()
    assert me["role"] == "ventas" and "reports" not in me["permissions"] and "anular" not in me["permissions"]["sales"]
    inv = client.post(
        "/invoices", json={"customer_id": c["id"], "lines": [{"name": "Venta propia", "quantity": 1, "unit_price": 10000, "tax_rate": 13}]}, headers=h
    )
    assert inv.status_code == 201
    dash = client.get("/dashboard", headers=h).json()
    assert dash["scope"] == "mine" and Decimal(str(dash["facturado"]["hoy"])) == Decimal("11300")
    assert [x["number"] for x in dash["facturas_recientes"]] == [inv.json()["number"]]
    assert client.post(f"/invoices/{inv.json()['id']}/void", headers=h).status_code == 403
    assert client.get("/reports/facturacion", headers=h).status_code == 403
    assert client.get("/expenses", headers=h).status_code == 403
    client.headers.update(admin_h)
    assert client.get("/dashboard").json()["scope"] == "company"


class FakeBccr:
    """Imita la API SDDE: 318 venta, 317 compra; sin datos el fin de semana."""

    def __init__(self, status=200):
        self.status, self.calls = status, []

    def get(self, url, params=None, headers=None, timeout=None):
        import httpx

        self.calls.append((url, params, headers))
        code = url.split("/indicadoresEconomicos/")[1].split("/")[0]
        val = {"318": 512.34, "317": 505.1}[code]
        body = {
            "estado": True,
            "mensaje": "Consulta exitosa",
            "datos": [
                {
                    "codigoIndicador": code,
                    "series": [{"fecha": "2026-09-11", "valorDatoPorPeriodo": val - 1}, {"fecha": "2026-09-12", "valorDatoPorPeriodo": val}],
                }
            ],
        }
        return httpx.Response(self.status, json=body, request=httpx.Request("GET", url))


def test_bccr_sdde_connector(client, auth, monkeypatch):
    from app.providers.fx import bccr

    monkeypatch.delenv("BCCR_TOKEN", raising=False)
    assert client.post("/fx/bccr").status_code == 422  # sin token: mensaje claro, nada se rompe
    monkeypatch.setenv("BCCR_TOKEN", "eyJ-prueba")
    fake = FakeBccr()
    sell, buy = bccr.fetch_today(date(2026, 9, 13), client=fake)  # domingo: toma el ultimo publicado
    assert (sell, buy) == (Decimal("512.34"), Decimal("505.1"))
    url, params, headers = fake.calls[0]
    assert url.endswith("/indicadoresEconomicos/318/series") and params["fechaFin"] == "2026/09/13" and headers["Authorization"] == "Bearer eyJ-prueba"
    import pytest

    with pytest.raises(bccr.BccrError, match="401"):
        bccr.fetch_today(date(2026, 9, 13), client=FakeBccr(401))
    monkeypatch.setattr(bccr.httpx, "get", FakeBccr().get)
    r = client.post("/fx/bccr")
    assert r.status_code == 200 and r.json()["source"] == "bccr"


def _user_with_role(db_session, tenant_id, email, role, pw="Rol-prueba-2026"):
    from app.core.security import hash_password
    from app.models import TenantUser, User

    u = User(email=email, full_name=role.capitalize(), password_hash=hash_password(pw))
    db_session.add(u)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=tenant_id, user_id=u.id, role_code=role))
    db_session.commit()
    return pw


def test_each_role_sees_only_what_it_needs(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    c = client.get("/customers").json()["items"][0]
    admin_inv = client.post("/invoices", json={"customer_id": c["id"], "lines": [{"name": "Admin", "quantity": 1, "unit_price": 1000, "tax_rate": 13}]}).json()
    client.post(f"/invoices/{admin_inv['id']}/payments", json={"method": "sinpe", "amount": 500})

    def login(email, role):
        pw = _user_with_role(db_session, tid, email, role)
        client.headers.pop("Authorization", None)
        tok = client.post("/auth/login", json={"email": email, "password": pw}).json()["access_token"]
        client.headers.update(admin_h)
        return {"Authorization": f"Bearer {tok}"}

    def report_keys(h):
        r = client.get("/reports", headers=h)
        return set() if r.status_code == 403 else {x["key"] for x in r.json()}

    # Vendedor: solo lo suyo
    v = login("v@ejemplo.com", "ventas")
    mine = client.post(
        "/invoices", json={"customer_id": c["id"], "lines": [{"name": "Propia", "quantity": 1, "unit_price": 2000, "tax_rate": 13}]}, headers=v
    ).json()
    assert [i["id"] for i in client.get("/invoices", headers=v).json()["items"]] == [mine["id"]]
    assert client.get(f"/invoices/{admin_inv['id']}", headers=v).status_code == 404
    assert client.get(f"/invoices/{admin_inv['id']}/pdf", headers=v).status_code == 404
    assert client.get("/payments", headers=v).json() == []
    assert all(h["id"] != admin_inv["id"] for h in client.get("/search", params={"q": admin_inv["number"]}, headers=v).json())
    assert client.get("/recurrences", headers=v).status_code == 403
    assert report_keys(v) == set()
    assert client.get("/payroll/employees", headers=v).status_code == 403

    # Caja: cualquier factura para cobrar, reportes solo de caja
    k = login("k@ejemplo.com", "caja")
    assert client.get(f"/invoices/{admin_inv['id']}", headers=k).status_code == 200
    assert report_keys(k) == {"cierre", "transacciones", "propinas"}
    assert client.get("/reports/resultados", headers=k).status_code == 403
    assert client.get("/dashboard", headers=k).json()["scope"] == "mine"
    assert client.get("/expenses", headers=k).status_code == 403

    # Bodega: inventario y nada de dinero
    b = login("b@ejemplo.com", "inventario")
    assert report_keys(b) == {"inventario", "movimientos"}
    assert client.get("/invoices", headers=b).status_code == 403
    assert client.get("/customers", headers=b).status_code == 403

    # Contabilidad: todo el dinero de la empresa, sin ajustes
    a = login("a@ejemplo.com", "contabilidad")
    assert len(report_keys(a)) == 19
    assert client.get("/dashboard", headers=a).json()["scope"] == "company"
    assert client.get("/settings", headers=a).status_code == 403

    # Solo lectura: ventas y cobros de la empresa, sin gastos, planillas ni exportar
    lec = login("l@ejemplo.com", "lectura")
    assert report_keys(lec) == {"facturacion", "pendientes", "productos", "ordenes", "cierre", "transacciones", "propinas", "rentabilidad", "rentabilidad_tipo"}
    assert client.get("/reports/facturacion", params={"format": "xlsx"}, headers=lec).status_code == 403
    assert client.get("/expenses", headers=lec).status_code == 403
    assert client.post("/customers", json={"name": "No"}, headers=lec).status_code == 403


def test_logout_all_revokes_every_session(client, auth):
    from app.models import RefreshToken

    assert client.post("/auth/refresh").status_code == 200
    assert client.post("/auth/logout-all").status_code == 204
    assert client.post("/auth/refresh").status_code == 401  # la cookie de este equipo ya no sirve
    db = client.app.dependency_overrides[next(iter(client.app.dependency_overrides))]()
    assert all(rt.revoked_at for rt in db.query(RefreshToken).all())


def test_discount_limit_and_approval(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    pw = _user_with_role(db_session, tid, "desc@ejemplo.com", "ventas")
    client.headers.pop("Authorization", None)
    v = {"Authorization": "Bearer " + client.post("/auth/login", json={"email": "desc@ejemplo.com", "password": pw}).json()["access_token"]}
    client.headers.update(admin_h)
    c = client.get("/customers").json()["items"][0]
    prod = client.get("/products", params={"q": "NVR"}).json()["items"][0]

    def quote(h, disc=0, price=None):
        ln = {"product_id": prod["id"], "quantity": 1, "discount_value": disc}
        if price is not None:
            ln["unit_price"] = price
        return client.post("/quotes", json={"customer_id": c["id"], "lines": [ln]}, headers=h).json()

    assert client.get("/sales/discount-limit", headers=v).json() == {"limit": 10, "free": False}
    assert quote(v, 5)["status"] == "creado"
    big = quote(v, 15)
    assert big["status"] == "por_aprobar"
    assert client.post(f"/quotes/{big['id']}/send", headers=v).status_code == 409
    assert client.post(f"/quotes/{big['id']}/convert", headers=v).status_code == 409
    assert client.post(f"/quotes/{big['id']}/approve", headers=v).status_code == 403  # el vendedor no se aprueba solo
    assert client.post(f"/quotes/{big['id']}/approve").json()["status"] == "creado"
    assert client.post(f"/quotes/{big['id']}/convert", headers=v).status_code == 201
    # bajar el precio de catalogo cuenta como descuento (189 000 -> 150 000 = 20.6 %)
    assert quote(v, 0, 150000)["status"] == "por_aprobar"
    # factura directa por encima del limite: rechazada con mensaje claro
    r = client.post("/invoices", json={"customer_id": c["id"], "lines": [{"product_id": prod["id"], "quantity": 1, "discount_value": 15}]}, headers=v)
    assert r.status_code == 422 and "límite es 10" in r.json()["detail"]
    assert (
        client.post(
            "/pos/sale",
            json={"lines": [{"product_id": prod["id"], "quantity": 1, "discount_value": 30}], "payments": [{"method": "efectivo", "amount": 999999}]},
            headers=v,
        ).status_code
        == 422
    )
    # el administrador no tiene limite y puede subir el limite del resto
    assert quote(admin_h, 50)["status"] == "creado"
    client.put("/settings", json={"max_discount_pct": 20})
    assert quote(v, 15)["status"] == "creado"
    # inicio del vendedor: sin cobros, con sus facturas por cobrar
    dash = client.get("/dashboard", headers=v).json()
    assert dash["pagos"] is None and dash["pagos_recientes"] == [] and dash["por_cobrar"]
    assert client.get("/dashboard").json()["acciones_pendientes"]["cotizaciones_por_aprobar"] == 1
