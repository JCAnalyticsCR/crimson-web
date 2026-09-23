"""Sesion 6: soporte al cliente, mantenimientos preventivos, comisiones, aprobacion por monto y margen,
y la reserva de material al abrir un proyecto. Todo salio de la grabacion de la reunion del 22/09."""

from datetime import date
from decimal import Decimal

from tests.test_session4 import _user_with_role


def _login(client, db_session, tid, email, rol, admin_h):
    pw = _user_with_role(db_session, tid, email, rol)
    client.headers.pop("Authorization", None)
    tok = client.post("/auth/login", json={"email": email, "password": pw}).json()["access_token"]
    client.headers.update(admin_h)
    return {"Authorization": f"Bearer {tok}"}


def test_ticket_de_soporte_de_punta_a_punta(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    cust = client.get("/customers").json()["items"][0]
    tec = _login(client, db_session, tid, "sop1@ejemplo.com", "tecnico", admin_h)

    meta = client.get("/tickets/meta/config").json()
    assert meta["sla_horas"]["critica"] == 2 and "garantia" in meta["kinds"]

    t = client.post(
        "/tickets",
        json={
            "subject": "No graba la cámara del portón",
            "customer_id": cust["id"],
            "kind": "soporte",
            "channel": "whatsapp",
            "priority": "alta",
            "description": "Desde el aguacero de ayer",
            "level": 1,
        },
    ).json()
    assert t["number"].startswith("TCK-") and str(date.today().year) in t["number"]
    assert t["status"] == "nuevo" and t["due_at"] and t["sla_vencido"] is False

    # se asigna al técnico y él responde: la primera respuesta detiene el reloj del SLA
    client.put(
        f"/tickets/{t['id']}",
        json={"subject": t["subject"], "customer_id": cust["id"], "kind": "soporte", "priority": "alta", "assigned_to": _uid(db_session, "sop1@ejemplo.com")},
    )
    assert client.get(f"/tickets/{t['id']}").json()["status"] == "asignado"
    con_nota = client.post(
        f"/tickets/{t['id']}/notes", headers=tec, json={"body": "Llamé al cliente, vamos mañana a las 9", "hours": 0.5, "status": "en_proceso"}
    ).json()
    assert con_nota["first_reply_at"] and Decimal(str(con_nota["hours"])) == Decimal("0.5") and len(con_nota["notes"]) == 1

    # hay que ir al sitio: el ticket genera la orden de trabajo, no se reescribe nada
    ot = client.post(f"/tickets/{t['id']}/work-order", json={"technician_id": _uid(db_session, "sop1@ejemplo.com"), "site": "Portón principal"}).json()
    assert ot["number"].startswith("OT-")
    assert client.get(f"/work-orders/{ot['work_order_id']}").json()["kind"] == "soporte"

    # no se cierra sin decir qué se hizo
    assert client.patch(f"/tickets/{t['id']}", headers=tec, json={"status": "resuelto"}).status_code == 422
    cerrado = client.patch(
        f"/tickets/{t['id']}", headers=tec, json={"status": "resuelto", "solution": "Fuente quemada, se cambió", "hours": 2, "billable": True, "amount": 45000}
    ).json()
    assert cerrado["status"] == "resuelto" and cerrado["resolved_at"]

    # el técnico solo ve lo suyo
    otro = client.post("/tickets", json={"subject": "Ticket de otra persona", "customer_id": cust["id"]}).json()
    mios = [x["id"] for x in client.get("/tickets", headers=tec).json()]
    assert t["id"] in mios and otro["id"] not in mios


def _uid(db, email):
    from sqlalchemy import select

    from app.models import User

    return db.scalar(select(User).where(User.email == email)).id


def test_mantenimiento_preventivo_abre_su_ticket_y_reprograma(client, auth):
    cust = client.get("/customers").json()["items"][0]
    c = client.post(
        "/contracts",
        json={
            "customer_id": cust["id"],
            "name": "CCTV condominio · preventivo",
            "every_months": 6,
            "next_date": str(date.today()),
            "amount": 90000,
            "scope": "Limpieza de lentes, revisión de grabación y respaldo",
        },
    ).json()
    assert c["number"].startswith("CTR-") and c["dias_para_la_proxima"] == 0

    t = client.post(f"/contracts/{c['id']}/ticket").json()
    assert t["kind"] == "mantenimiento" and t["billable"] is True and Decimal(str(t["amount"])) == 90000
    despues = client.get("/contracts").json()[0]
    assert despues["last_done"] == str(date.today()) and despues["next_date"] > str(date.today())

    # ya no le toca: el worker no lo vuelve a abrir hasta dentro de seis meses
    assert client.post(f"/contracts/{c['id']}/ticket").status_code == 201  # a mano siempre se puede


def test_comision_nace_con_el_cobro_no_con_la_factura(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    cust = client.get("/customers").json()["items"][0]
    v = _login(client, db_session, tid, "comi@ejemplo.com", "ventas", admin_h)
    uid = _uid(db_session, "comi@ejemplo.com")

    client.post("/commissions/rules", json={"user_id": uid, "name": "Vendedora", "base": "venta", "percent": 5})
    inv = client.post(
        "/invoices", headers=v, json={"customer_id": cust["id"], "lines": [{"name": "Instalación CCTV", "quantity": 1, "unit_price": 1000000, "tax_rate": 13}]}
    ).json()

    assert client.get("/commissions").json()["rows"] == []  # facturar no comisiona: hay que cobrar

    client.post(f"/invoices/{inv['id']}/payments", json={"method": "transferencia", "amount": 500000})
    com = client.get("/commissions").json()
    assert len(com["rows"]) == 1
    fila = com["rows"][0]
    assert fila["user_id"] == uid and Decimal(str(fila["amount"])) == Decimal("25000.00")  # 5 % de lo cobrado
    assert Decimal(str(com["totals"]["pendiente"])) == Decimal("25000.00")

    # cada quien ve la suya y no puede aprobarla
    assert len(client.get("/commissions", headers=v).json()["rows"]) == 1
    assert client.patch("/commissions", headers=v, json={"ids": [fila["id"]], "status": "pagada"}).status_code == 403
    client.patch("/commissions", json={"ids": [fila["id"]], "status": "pagada"})
    assert client.get("/commissions").json()["rows"][0]["status"] == "pagada"

    # el resto del cobro comisiona aparte; el mismo pago no comisiona dos veces
    client.post(f"/invoices/{inv['id']}/payments", json={"method": "transferencia", "amount": 630000})
    assert len(client.get("/commissions").json()["rows"]) == 2


def test_cotizacion_grande_o_con_margen_bajo_necesita_aprobacion(client, auth, db_session):
    tid = auth["tenant"]["id"]
    admin_h = dict(client.headers)
    cust = client.get("/customers").json()["items"][0]
    cam = client.get("/products", params={"q": "CAM-DOME"}).json()["items"][0]
    from app.models import Product

    db_session.get(Product, cam["id"]).cost = Decimal("80")  # USD
    db_session.commit()
    v = _login(client, db_session, tid, "vend2@ejemplo.com", "ventas", admin_h)

    # 1) por monto: el tope son 2 millones
    grande = client.post(
        "/quotes", headers=v, json={"customer_id": cust["id"], "lines": [{"name": "Proyecto grande", "quantity": 1, "unit_price": 2500000, "tax_rate": 13}]}
    ).json()
    assert grande["status"] == "por_aprobar" and "supera el tope" in grande["approval_reason"]

    # 2) por margen: la cámara cuesta 80 USD (≈41 000) y se vende en 45 000 -> 9 % de margen
    flaca = client.post(
        "/quotes",
        headers=v,
        json={"customer_id": cust["id"], "lines": [{"product_id": cam["id"], "name": cam["name"], "quantity": 1, "unit_price": 45000, "tax_rate": 13}]},
    ).json()
    assert flaca["status"] == "por_aprobar" and "margen" in flaca["approval_reason"]

    # 3) una normal sale sola
    sana = client.post(
        "/quotes",
        headers=v,
        json={"customer_id": cust["id"], "lines": [{"product_id": cam["id"], "name": cam["name"], "quantity": 1, "unit_price": 90000, "tax_rate": 13}]},
    ).json()
    assert sana["status"] == "creado" and sana["approval_reason"] is None

    # quien aprueba no se pide permiso a sí mismo
    del_admin = client.post(
        "/quotes", json={"customer_id": cust["id"], "lines": [{"name": "Proyecto grande", "quantity": 1, "unit_price": 9000000, "tax_rate": 13}]}
    ).json()
    assert del_admin["status"] == "creado"

    aprobada = client.post(f"/quotes/{grande['id']}/approve").json()
    assert aprobada["status"] == "creado" and aprobada["approval_reason"] is None


def test_proyecto_avisa_lo_que_hay_que_comprar(client, auth):
    """Los proveedores no dan crédito: el faltante se sabe al abrir el proyecto, no el día de la instalación."""
    cust = client.get("/customers").json()["items"][0]
    cam = client.get("/products", params={"q": "CAM-DOME"}).json()["items"][0]
    q = client.post(
        "/quotes",
        json={"customer_id": cust["id"], "lines": [{"product_id": cam["id"], "name": cam["name"], "quantity": 99, "unit_price": 90000, "tax_rate": 13}]},
    ).json()
    pr = client.post(f"/quotes/{q['id']}/project", json={}).json()
    assert pr["por_comprar"] and Decimal(str(pr["por_comprar"][0]["to_buy"])) > 0
    solicitudes = client.get("/purchase-requests").json()
    assert solicitudes and solicitudes[0]["reason"] == "proyecto" and solicitudes[0]["project"] == pr["number"]


def test_2fa_con_codigos_de_recuperacion(client, auth, db_session):
    """Perder el teléfono no puede significar perder la cuenta: los códigos de un solo uso y el botón
    de desactivar son la salida. Antes solo se arreglaba entrando a la base de datos."""
    import pyotp

    from app.models import User

    me = client.get("/auth/me").json()
    setup = client.post("/auth/2fa/setup", json={}).json()
    codigo = pyotp.TOTP(setup["secret"]).now()
    codes = client.post("/auth/2fa/verify", json={"code": codigo}).json()["codes"]
    assert len(codes) == 10 and all(len(c) == 9 and "-" in c for c in codes)
    assert client.get("/auth/me").json()["user"]["totp_enabled"] is True

    # sin código no entra; con el de la app sí
    correo, pw = me["user"]["email"], "Crimson-2026-seguro"
    client.headers.pop("Authorization", None)
    sin = client.post("/auth/login", json={"email": correo, "password": pw})
    assert sin.status_code == 401 and sin.headers.get("X-2FA") == "required"
    con = client.post("/auth/login", json={"email": correo, "password": pw, "totp_code": pyotp.TOTP(setup["secret"]).now()})
    assert con.status_code == 200

    # el teléfono se perdió: entra con un código de recuperación, y ese código no sirve dos veces
    entro = client.post("/auth/login", json={"email": correo, "password": pw, "totp_code": codes[0]})
    assert entro.status_code == 200
    repetido = client.post("/auth/login", json={"email": correo, "password": pw, "totp_code": codes[0]})
    assert repetido.status_code == 401
    assert len(db_session.get(User, me["user"]["id"]).totp_recovery) == 9

    # minúsculas y sin guion también valen: se van a dictar por teléfono
    assert client.post("/auth/login", json={"email": correo, "password": pw, "totp_code": codes[1].lower().replace("-", "")}).status_code == 200

    # y se puede apagar con la contraseña
    client.headers.update({"Authorization": "Bearer " + entro.json()["access_token"]})
    assert client.post("/auth/2fa/disable", json={"password": "otra-cosa"}).status_code == 400
    assert client.post("/auth/2fa/disable", json={"password": pw}).status_code == 204
    u = db_session.get(User, me["user"]["id"])
    assert u.totp_enabled is False and u.totp_secret is None and u.totp_recovery == []
    assert client.post("/auth/login", json={"email": correo, "password": pw}).status_code == 200
