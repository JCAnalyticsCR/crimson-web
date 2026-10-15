"""Recurrentes: dia ancla (un 31 no baja al 28; el 29 de febrero no revienta), idempotencia por periodo, tipo de cambio
de la fecha de cada periodo, ponerse al dia, una que falla no frena a las demas, permisos por tipo; categorias."""

from datetime import date, timedelta
from decimal import Decimal

from app.routers.ops import _next


def test_next_con_ancla():
    d = date(2027, 1, 31)
    seq = [d]
    for _ in range(2):
        seq.append(_next(seq[-1], "mensual", 31))
    assert seq == [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31)]
    a = [date(2028, 2, 29)]
    for _ in range(4):
        a.append(_next(a[-1], "anual", 29))
    assert a == [date(2028, 2, 29), date(2029, 2, 28), date(2030, 2, 28), date(2031, 2, 28), date(2032, 2, 29)]
    q = [date(2027, 1, 5)]
    for _ in range(2):
        q.append(_next(q[-1], "quincenal", 5))
    assert q == [date(2027, 1, 5), date(2027, 1, 20), date(2027, 2, 5)]
    q = [date(2027, 1, 31)]
    for _ in range(4):
        q.append(_next(q[-1], "quincenal", 31))
    assert q == [date(2027, 1, 31), date(2027, 2, 16), date(2027, 2, 28), date(2027, 3, 16), date(2027, 3, 31)]
    assert _next(date(2027, 1, 1), "semanal") == date(2027, 1, 8)


def _rec(client, **kw):
    body = {"name": "Hosting", "kind": "gasto", "template": {"description": "Hosting del sitio", "subtotal": 100, "currency": "USD"}, "frequency": "mensual", "next_date": date.today().isoformat()}
    body.update(kw)
    r = client.post("/recurrences", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_usd_con_tc_de_cada_periodo_y_sin_credito_por_defecto(client, auth, db_session):
    from app.models import ExchangeRate, Expense
    from app.routers.ops import run_due_recurrences

    hoy = date.today()
    p1 = (hoy.replace(day=1) - timedelta(days=40)).replace(day=10)
    p2 = (p1.replace(day=28) + timedelta(days=4)).replace(day=10)
    db_session.add_all([ExchangeRate(date=p1, currency="USD", sell=Decimal("500.00"), buy=Decimal("495")), ExchangeRate(date=p2, currency="USD", sell=Decimal("510.00"), buy=Decimal("505"))])
    db_session.commit()
    rec = _rec(client, next_date=p1.isoformat())
    assert rec["template"]["iva_credit"] == "no_credito" and Decimal(str(rec["template"]["tax_rate"])) == 0 and rec["anchor_day"] == 10
    n = run_due_recurrences(db_session)
    gastos = db_session.query(Expense).filter(Expense.recurrence_id == rec["id"]).order_by(Expense.recurrence_date).all()
    assert n >= 2 and gastos[0].recurrence_date == p1 and Decimal(str(gastos[0].fx_rate)) == Decimal("500")
    assert gastos[1].recurrence_date == p2 and Decimal(str(gastos[1].fx_rate)) == Decimal("510")
    assert all(g.iva_credit == "no_credito" for g in gastos)
    # correr de nuevo el mismo dia: nada nuevo
    assert run_due_recurrences(db_session) == 0
    assert db_session.query(Expense).filter(Expense.recurrence_id == rec["id"]).count() == len(gastos)


def test_idempotente_por_periodo_y_unico_de_base(client, auth, db_session):
    from app.models import Expense, Recurrence

    rec = _rec(client, template={"description": "Alquiler", "subtotal": 1000})
    hoy = date.today()
    # otro proceso ya genero el periodo (fila insertada a mano): correr no duplica
    db_session.add(Expense(tenant_id=auth["tenant"]["id"], description="Alquiler", date=hoy, subtotal=1000, tax_rate=0, tax_amount=0, total=1000, recurrence_id=rec["id"], recurrence_date=hoy))
    db_session.commit()
    r = client.post(f"/recurrences/{rec['id']}/run")
    assert r.status_code == 201
    assert db_session.query(Expense).filter(Expense.recurrence_id == rec["id"]).count() == 1
    db_session.refresh(db_session.get(Recurrence, rec["id"]))
    assert db_session.get(Recurrence, rec["id"]).next_date > hoy


def test_ponerse_al_dia_y_facturas_de_a_una(client, auth, db_session):
    from app.models import EmailOutbox, Expense, Invoice, Recurrence
    from app.routers.ops import run_due_recurrences

    hoy = date.today()
    gasto = _rec(client, frequency="semanal", next_date=(hoy - timedelta(days=7 * 20)).isoformat(), template={"description": "Limpieza", "subtotal": 10})
    cust = client.get("/customers").json()["items"][0]
    fac = client.post(
        "/recurrences",
        json={"name": "Mantenimiento", "kind": "factura", "customer_id": cust["id"], "template": {"lines": [{"name": "Mantenimiento", "quantity": 1, "unit_price": 1000, "tax_rate": 13, "cabys_code": "8121100000100"}]}, "frequency": "mensual", "next_date": (hoy - timedelta(days=70)).isoformat()},
    ).json()
    antes_mail = db_session.query(EmailOutbox).count()
    run_due_recurrences(db_session)
    assert db_session.query(Expense).filter(Expense.recurrence_id == gasto["id"]).count() == 12  # max. 12 por corrida
    r = db_session.get(Recurrence, fac["id"])
    db_session.refresh(r)
    assert r.runs == 1 and db_session.query(Invoice).filter(Invoice.id == r.last_invoice_id).count() == 1
    assert db_session.query(EmailOutbox).count() > antes_mail  # alerta: periodos atrasados


def test_una_que_falla_no_frena_a_las_demas(client, auth, db_session, monkeypatch):
    from app.models import Expense
    from app.routers import ops

    a = _rec(client, name="A", template={"description": "A", "subtotal": 1})
    b = _rec(client, name="B", template={"description": "B", "subtotal": 2})
    real = ops.run_recurrence

    def falla(db, r, uid):
        if r.id == a["id"]:
            raise RuntimeError("plantilla rota")
        return real(db, r, uid)

    monkeypatch.setattr(ops, "run_recurrence", falla)
    ops.run_due_recurrences(db_session)
    assert db_session.query(Expense).filter(Expense.recurrence_id == b["id"]).count() == 1
    assert db_session.query(Expense).filter(Expense.recurrence_id == a["id"]).count() == 0


def test_permisos_por_tipo(client, auth, db_session):
    from app.core.security import hash_password
    from app.models import Role, TenantUser, User
    from tests.hacienda_utils import role_headers

    tid = auth["tenant"]["id"]
    conta = role_headers(client, db_session, tid, "conta.rec@ejemplo.com", "contabilidad")
    assert client.post("/recurrences", headers=conta, json={"name": "Hosting", "kind": "gasto", "template": {"description": "H", "subtotal": 5}, "next_date": date.today().isoformat()}).status_code == 201
    db_session.add(Role(code="solo_recurrencias", name="Solo recurrencias", permissions={"sales": ["recurrencias"], "dashboard": ["ver"]}))
    u = User(email="rec.facturas@ejemplo.com", full_name="Recurrencias", password_hash=hash_password("Rol-prueba-2026"))
    db_session.add(u)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=tid, user_id=u.id, role_code="solo_recurrencias"))
    db_session.commit()
    admin = client.headers.pop("Authorization")
    tok = client.post("/auth/login", json={"email": "rec.facturas@ejemplo.com", "password": "Rol-prueba-2026"}).json()["access_token"]
    client.headers["Authorization"] = admin
    h = {"Authorization": f"Bearer {tok}"}
    vis = client.get("/recurrences", headers=h).json()
    assert all(r["kind"] == "factura" for r in vis)
    assert client.post("/recurrences", headers=h, json={"name": "X", "kind": "gasto", "template": {"description": "X", "subtotal": 1}, "next_date": date.today().isoformat()}).status_code == 403
    assert all(r["kind"] == "gasto" for r in client.get("/recurrences", params={"kind": "gasto"}).json())


def test_categorias_renombrar_desactivar_y_d151(client, auth):
    c = client.post("/expense-categories", json={"name": "Honorarios", "d151_kind": "servicios_profesionales"}).json()
    r = client.put(f"/expense-categories/{c['id']}", json={"name": "Honorarios profesionales", "active": False, "d151_kind": "servicios_profesionales"})
    assert r.status_code == 200 and r.json()["name"] == "Honorarios profesionales" and not r.json()["active"]
    assert all(x["id"] != c["id"] for x in client.get("/expense-categories", params={"active": 1}).json())
    assert any(x["id"] == c["id"] and x["d151_kind"] == "servicios_profesionales" for x in client.get("/expense-categories").json())
    assert client.put(f"/expense-categories/{c['id']}", json={"name": "X", "d151_kind": "otra_cosa"}).status_code == 422
