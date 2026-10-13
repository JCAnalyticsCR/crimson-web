"""Inventario propio: movimientos enriquecidos, stock negativo opcional, reservas formales (AC06/AC25) y seriales."""

import os
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.models import CustomerAsset, Product, StockMovement, StockReservation, Tenant, Warehouse
from app.seeds import seed
from app.services import inventory as invsvc
from app.services import reservations as rsv

from .conftest import ADMIN


def _new_product(client, code="RES-1", name="Cámara reservas"):
    r = client.post("/products", json={"name": name, "code": code, "price": 100, "cost": 50})
    assert r.status_code == 201, r.text
    return r.json()


def _wh(client):
    return next(w for w in client.get("/warehouses").json() if w["is_default"])


def _entrada(client, pid, wid, qty):
    r = client.post("/stock/movements", json={"product_id": pid, "warehouse_id": wid, "kind": "entrada", "quantity": qty})
    assert r.status_code == 201, r.text


def _level(client, pid, wid):
    return next(lv for lv in client.get("/stock").json()["levels"] if lv["product_id"] == pid and lv["warehouse_id"] == wid)


def _login_role(client, db_session, tid, email, role):
    from app.core.security import hash_password
    from app.models import TenantUser, User

    u = User(email=email, full_name=role.capitalize(), password_hash=hash_password("Rol-prueba-2026"))
    db_session.add(u)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=tid, user_id=u.id, role_code=role))
    db_session.commit()
    admin = dict(client.headers)
    client.headers.pop("Authorization", None)
    tok = client.post("/auth/login", json={"email": email, "password": "Rol-prueba-2026"}).json()["access_token"]
    client.headers.update(admin)
    return {"Authorization": f"Bearer {tok}"}


# ---------- A) movimientos enriquecidos, compatibles ----------
def test_movimiento_enriquecido_y_el_de_siempre(client, auth):
    prod = _new_product(client)
    w = _wh(client)
    _entrada(client, prod["id"], w["id"], 3)  # el de siempre, sin campos nuevos
    cust = client.get("/customers").json()["items"][0]
    pr = client.post("/projects", json={"name": "Obra enriquecida", "customer_id": cust["id"]}).json()
    r = client.post(
        "/stock/movements",
        json={
            "product_id": prod["id"],
            "warehouse_id": w["id"],
            "kind": "salida",
            "quantity": 1,
            "project_id": pr["id"],
            "serial": "SN-001",
            "movement_type": "consumo_proyecto",
            "voucher_media": "/media/abc/comprobante.pdf",
            "transfer_price": 55.5,
            "custodian": "Técnico Juan",
            "location": "Estante A3",
        },
    )
    assert r.status_code == 201, r.text
    rows = client.get("/stock/movements", params={"product_id": prod["id"]}).json()
    assert rows[0]["movement_type"] == "consumo_proyecto" and rows[0]["project_id"] == pr["id"] and rows[0]["serial"] == "SN-001"
    assert rows[0]["custodian"] == "Técnico Juan" and rows[0]["location"] == "Estante A3" and Decimal(str(rows[0]["transfer_price"])) == Decimal("55.5")
    assert rows[1]["movement_type"] is None and rows[1]["owner"] is None  # el viejo queda igual
    bad = client.post("/stock/movements", json={"product_id": prod["id"], "warehouse_id": w["id"], "kind": "salida", "quantity": 1, "movement_type": "robo"})
    assert bad.status_code == 422


# ---------- E) stock negativo: apagado nada cambia, encendido rechaza salidas sin saldo ----------
def test_impedir_stock_negativo_es_opcional(client, auth, db_session):
    prod = _new_product(client)
    w = _wh(client)
    assert client.get("/settings").json()["prevent_negative_stock"] is False
    # apagado: la salida sin saldo pasa como siempre (ajustes, OT, ventas dejan negativo)
    r = client.post("/stock/movements", json={"product_id": prod["id"], "warehouse_id": w["id"], "kind": "salida", "quantity": 2})
    assert r.status_code == 201
    assert Decimal(str(_level(client, prod["id"], w["id"])["quantity"])) == -2
    # encendido: salida sin saldo -> 409, entrada y salida con saldo siguen
    assert client.put("/settings", json={"prevent_negative_stock": True}).status_code == 200
    r = client.post("/stock/movements", json={"product_id": prod["id"], "warehouse_id": w["id"], "kind": "salida", "quantity": 1})
    assert r.status_code == 409 and "Impedir stock negativo" in r.json()["detail"]
    _entrada(client, prod["id"], w["id"], 5)  # queda en 3
    assert client.post("/stock/movements", json={"product_id": prod["id"], "warehouse_id": w["id"], "kind": "salida", "quantity": 3}).status_code == 201
    assert client.post("/stock/movements", json={"product_id": prod["id"], "warehouse_id": w["id"], "kind": "ajuste", "quantity": -1}).status_code == 409
    t = db_session.get(Tenant, auth["tenant"]["id"])
    with pytest.raises(HTTPException) as e:
        invsvc.move(db_session, t.id, None, prod["id"], w["id"], "venta", Decimal(-1))
    assert e.value.status_code == 409
    db_session.rollback()
    # se apaga y vuelve el comportamiento de siempre
    client.put("/settings", json={"prevent_negative_stock": False})
    assert client.post("/stock/movements", json={"product_id": prod["id"], "warehouse_id": w["id"], "kind": "salida", "quantity": 4}).status_code == 201


# ---------- C/D) reservas: pendiente -> aprobacion -> comprometido ----------
def test_reserva_requiere_aprobacion_y_compromete(client, auth, db_session):
    tid = auth["tenant"]["id"]
    prod = _new_product(client)
    w = _wh(client)
    _entrada(client, prod["id"], w["id"], 5)
    cust = client.get("/customers").json()["items"][0]
    pr = client.post("/projects", json={"name": "Obra reservas", "customer_id": cust["id"]}).json()

    r = client.post("/stock/reservations", json={"product_id": prod["id"], "quantity": 3, "project_id": pr["id"], "reason": "Instalación"})
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["status"] == "pendiente" and res["warehouse_id"] == w["id"] and res["expires_at"]
    # AC25: ver existencia no es retirar; una pendiente no compromete
    lv = _level(client, prod["id"], w["id"])
    assert Decimal(str(lv["committed"])) == 0 and Decimal(str(lv["available"])) == 5

    # el tecnico ve inventario pero no aprueba
    tk = _login_role(client, db_session, tid, "tec-res@ejemplo.com", "tecnico")
    assert client.post(f"/stock/reservations/{res['id']}/confirm", headers=tk).status_code == 403
    assert client.post("/stock/reservations", headers=tk, json={"product_id": prod["id"], "quantity": 1}).status_code == 403
    # el supervisor si aprueba
    sup = _login_role(client, db_session, tid, "sup-res@ejemplo.com", "supervisor")
    ok = client.post(f"/stock/reservations/{res['id']}/confirm", headers=sup)
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "confirmada" and ok.json()["approved_by"] == "Supervisor"
    lv = _level(client, prod["id"], w["id"])
    assert Decimal(str(lv["committed"])) == 3 and Decimal(str(lv["available"])) == 2

    # otra reserva que pide mas de lo libre: 409 claro, nunca saldo de reservas negativo
    r2 = client.post("/stock/reservations", json={"product_id": prod["id"], "quantity": 3}).json()
    no = client.post(f"/stock/reservations/{r2['id']}/confirm")
    assert no.status_code == 409 and "libre 2" in no.json()["detail"]
    # confirmar dos veces la misma: 409
    assert client.post(f"/stock/reservations/{res['id']}/confirm").status_code == 409
    # cancelar libera
    assert client.post(f"/stock/reservations/{res['id']}/cancel", json={"note": "cambio de alcance"}).json()["status"] == "cancelada"
    assert client.post(f"/stock/reservations/{r2['id']}/confirm").json()["status"] == "confirmada"
    lista = client.get("/stock/reservations", params={"status": "abiertas"}).json()
    assert lista["can_approve"] is True and [x["id"] for x in lista["items"]] == [r2["id"]]
    # el ledger no se toco: reservar no mueve existencia
    assert Decimal(str(_level(client, prod["id"], w["id"])["quantity"])) == 5


def test_regla_automatica_y_vencimiento(client, auth, db_session):
    prod = _new_product(client)
    w = _wh(client)
    _entrada(client, prod["id"], w["id"], 4)
    cust = client.get("/customers").json()["items"][0]
    pr = client.post("/projects", json={"name": "Obra auto", "customer_id": cust["id"]}).json()
    client.put("/settings", json={"reservation_auto_approve_units": 2})
    auto = client.post("/stock/reservations", json={"product_id": prod["id"], "quantity": 2, "project_id": pr["id"]}).json()
    assert auto["status"] == "confirmada" and auto["approved_by"] == "Regla automática"
    grande = client.post("/stock/reservations", json={"product_id": prod["id"], "quantity": 3, "project_id": pr["id"]}).json()
    assert grande["status"] == "pendiente"  # supera el limite: requiere aprobacion
    sin_proyecto = client.post("/stock/reservations", json={"product_id": prod["id"], "quantity": 1}).json()
    assert sin_proyecto["status"] == "pendiente"  # la regla es para proyectos propios
    # la regla no salta la disponibilidad: si no alcanza queda pendiente con aviso
    otra = client.post("/stock/reservations", json={"product_id": prod["id"], "quantity": 2, "project_id": pr["id"]}).json()
    assert otra["status"] == "confirmada"
    sin_lugar = client.post("/stock/reservations", json={"product_id": prod["id"], "quantity": 1, "project_id": pr["id"]}).json()
    assert sin_lugar["status"] == "pendiente" and "No alcanza" in sin_lugar["note"]

    # vencimiento (tarea del worker): libera lo comprometido
    r = db_session.get(StockReservation, auto["id"])
    r.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    assert Decimal(str(_level(client, prod["id"], w["id"])["committed"])) == 2  # la vencida ya no cuenta aunque el worker no haya pasado
    n = rsv.expire_due(db_session)
    assert n == 1 and db_session.get(StockReservation, auto["id"]).status == "vencida"
    # una pendiente vencida no se puede confirmar
    g = db_session.get(StockReservation, grande["id"])
    g.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    assert client.post(f"/stock/reservations/{grande['id']}/confirm").status_code == 409
    assert db_session.get(StockReservation, grande["id"]).status == "vencida"


def test_worker_vence_reservas(db_session, monkeypatch):
    import sys

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    pytest.importorskip("celery")
    sys.path.insert(0, os.path.join(root, "worker"))
    try:
        import celery_app
    finally:
        sys.path.pop(0)
    assert "expire-reservations" in celery_app.celery.conf.beat_schedule
    monkeypatch.setattr("app.core.db.SessionLocal", lambda: _Ctx(db_session))
    assert celery_app.expire_reservations() == 0


class _Ctx:
    def __init__(self, s):
        self.s = s

    def __enter__(self):
        return self.s

    def __exit__(self, *a):
        return False


# ---------- proyecto: reservar desde lo planificado, _reservar y consumo en la OT ----------
def test_proyecto_reserva_planificado_y_la_ot_la_consume(client, auth, db_session):
    prod = _new_product(client)
    w = _wh(client)
    _entrada(client, prod["id"], w["id"], 10)
    cust = client.get("/customers").json()["items"][0]
    pr = client.post("/projects", json={"name": "Obra con OT", "customer_id": cust["id"]}).json()
    ot = client.post(
        "/work-orders",
        json={"title": "Instalación", "project_id": pr["id"], "materials": [{"product_id": prod["id"], "name": prod["name"], "quantity": 0, "planned": 4}]},
    ).json()
    out = client.post(f"/projects/{pr['id']}/reserve", json={}).json()
    assert len(out["created"]) == 1 and Decimal(str(out["created"][0]["quantity"])) == 4 and out["created"][0]["status"] == "pendiente"
    # pedirla otra vez no duplica
    assert client.post(f"/projects/{pr['id']}/reserve", json={}).json()["skipped"][0]["motivo"] == "ya reservado"
    rid = out["created"][0]["id"]
    assert client.post(f"/stock/reservations/{rid}/confirm").json()["status"] == "confirmada"
    assert [x["id"] for x in client.get(f"/projects/{pr['id']}/reservations").json()] == [rid]

    # la OT usa 3 de 4: inventario baja 3 (como siempre) y la reserva se consume; sin mas OT abiertas, se cierra
    client.post(f"/work-orders/{ot['id']}/start", json={})
    fin = client.post(f"/work-orders/{ot['id']}/finish", json={"materials": [{"product_id": prod["id"], "name": prod["name"], "quantity": 3, "planned": 4}]})
    assert fin.status_code == 200, fin.text
    assert Decimal(str(_level(client, prod["id"], w["id"])["quantity"])) == 7
    r = db_session.get(StockReservation, rid)
    db_session.refresh(r)
    assert r.status == "consumida" and Decimal(str(r.consumed_quantity)) == 3
    assert Decimal(str(_level(client, prod["id"], w["id"])["committed"])) == 0


def test_reservar_al_abrir_proyecto_usa_reservas_y_conserva_el_virtual(client, auth, db_session):
    """_reservar: sin reservas formales calcula como siempre; con reservas confirmadas usa esas."""
    from types import SimpleNamespace

    from app.models import Project, WorkOrder, WorkOrderMaterial
    from app.routers.projects import _reservar

    tid = auth["tenant"]["id"]
    prod = _new_product(client)
    w = _wh(client)
    _entrada(client, prod["id"], w["id"], 5)
    cust = client.get("/customers").json()["items"][0]
    otro = client.post("/projects", json={"name": "Otro proyecto", "customer_id": cust["id"]}).json()
    client.post(
        "/work-orders",
        json={"title": "OT abierta", "project_id": otro["id"], "materials": [{"product_id": prod["id"], "name": prod["name"], "quantity": 0, "planned": 3}]},
    )
    ot = db_session.scalar(select(WorkOrder).where(WorkOrder.project_id == otro["id"]))
    ot.status = "asignada"
    db_session.commit()

    nuevo = client.post("/projects", json={"name": "Nuevo", "customer_id": cust["id"]}).json()
    pr = db_session.get(Project, nuevo["id"])
    order = WorkOrder(tenant_id=tid, number="OT-X", project_id=pr.id, title="Nueva", kind="instalacion")
    order.materials.append(WorkOrderMaterial(product_id=prod["id"], name=prod["name"], quantity=0, planned=4))
    p = SimpleNamespace(tenant=db_session.get(Tenant, tid), user=SimpleNamespace(id=None), ip=None)

    # virtual (como hoy): 5 en bodega - 3 apartados por la OT abierta = 2 libres -> faltan 2
    import app.routers.projects as projmod

    calls = []
    orig = projmod._new_request
    projmod._new_request = lambda db, p, rows, **k: calls.append(rows) or {"number": "SC-T", "id": 0}
    orig_notify = projmod.notify_roles
    projmod.notify_roles = lambda *a, **k: None
    try:
        faltan = _reservar(db_session, p, pr, order)
        assert Decimal(str(faltan[0]["to_buy"])) == 2
        # reserva formal confirmada del otro proyecto por 1: manda la reserva (no el virtual de 3) -> faltan 0
        r = rsv.create(db_session, tid, None, prod["id"], Decimal(1), project_id=otro["id"])
        db_session.commit()
        rsv.confirm(db_session, r, None)
        assert _reservar(db_session, p, pr, order) == []
        # reserva sin proyecto por 3 mas: 5 - 1 - 3 = 1 libre -> faltan 3
        r2 = rsv.create(db_session, tid, None, prod["id"], Decimal(3))
        db_session.commit()
        rsv.confirm(db_session, r2, None)
        assert Decimal(str(_reservar(db_session, p, pr, order)[0]["to_buy"])) == 3
    finally:
        projmod._new_request = orig
        projmod.notify_roles = orig_notify
        db_session.rollback()


# ---------- B) unidades serializadas ----------
def test_seriales_alta_estado_e_instalacion(client, auth, db_session):
    prod = _new_product(client, "NVR-1", "NVR 8 canales")
    w = _wh(client)
    r = client.post("/stock/units", json={"product_id": prod["id"], "serials": ["NVR-A1", "NVR-A2", " NVR-A2 "], "register_entry": True, "location": "Rack 1"})
    assert r.status_code == 201, r.text
    assert len(r.json()) == 2 and r.json()[0]["status"] == "disponible"
    assert db_session.get(Product, prod["id"]).serialized is True
    assert Decimal(str(_level(client, prod["id"], w["id"])["quantity"])) == 2  # register_entry sumo la entrada
    assert client.post("/stock/units", json={"product_id": prod["id"], "serials": ["NVR-A1"]}).status_code == 409  # unico por empresa
    # sin register_entry no toca el ledger (la compra ya entro por otra via)
    client.post("/stock/units", json={"product_id": prod["id"], "serials": ["NVR-A3"]})
    assert Decimal(str(_level(client, prod["id"], w["id"])["quantity"])) == 2

    cust = client.get("/customers").json()["items"][0]
    pr = client.post("/projects", json={"name": "Obra NVR", "customer_id": cust["id"]}).json()
    u = client.get("/stock/units", params={"q": "A1"}).json()[0]
    assert client.patch(f"/stock/units/{u['id']}", json={"status": "instalado"}).status_code == 422  # sin proyecto
    assert client.patch(f"/stock/units/{u['id']}", json={"status": "volando"}).status_code == 422
    ok = client.patch(f"/stock/units/{u['id']}", json={"status": "instalado", "project_id": pr["id"], "asset_location": "Cuarto de TI"}).json()
    assert ok["status"] == "instalado" and ok["customer_asset_id"]
    a = db_session.get(CustomerAsset, ok["customer_asset_id"])
    assert a.serial == "NVR-A1" and a.customer_id == cust["id"] and a.project_id == pr["id"] and a.location == "Cuarto de TI"
    # si el activo ya existia con esa serie, se liga en vez de duplicar
    u2 = client.get("/stock/units", params={"q": "A2"}).json()[0]
    pre = client.post("/assets", json={"customer_id": cust["id"], "name": "NVR ya registrado", "serial": "NVR-A2"}).json()
    ok2 = client.patch(f"/stock/units/{u2['id']}", json={"status": "instalado", "project_id": pr["id"]}).json()
    assert ok2["customer_asset_id"] == pre["id"]
    assert client.patch(f"/products/{prod['id']}/serialized", json={"serialized": False}).json()["serialized"] is False


# ---------- AC06: dos confirmaciones simultaneas sobre la ultima unidad ----------
def _race(make_session, tenant_id, product_id, warehouse_id, n_threads=2):
    """Crea n reservas pendientes de 1 unidad y las confirma a la vez desde hilos con sesiones propias."""
    s = make_session()
    ids = []
    for _ in range(n_threads):
        r = rsv.create(s, tenant_id, None, product_id, Decimal(1), warehouse_id)
        ids.append(r.id)
    s.commit()
    s.close()
    barrier = threading.Barrier(n_threads)
    results: dict[int, object] = {}

    def worker(rid):
        db = make_session()
        try:
            r = db.get(StockReservation, rid)
            barrier.wait()
            rsv.confirm(db, r, None)
            results[rid] = "ok"
        except HTTPException as e:
            results[rid] = e.status_code
        except Exception as e:  # noqa: BLE001
            results[rid] = repr(e)
        finally:
            db.close()

    ts = [threading.Thread(target=worker, args=(rid,)) for rid in ids]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    return results


def _setup_race_db(url):
    engine = create_engine(url)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _fk(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    seed(s, *ADMIN)
    t = s.scalar(select(Tenant))
    w = s.scalar(select(Warehouse).where(Warehouse.tenant_id == t.id, Warehouse.is_default.is_(True)))
    p = Product(tenant_id=t.id, name="Última cámara", code="ULT-1", item_type="producto", price=1)
    s.add(p)
    s.flush()
    invsvc.move(s, t.id, None, p.id, w.id, "entrada", Decimal(1))
    s.commit()
    ids = (t.id, p.id, w.id)
    s.close()
    return engine, Session, ids


def _assert_one_wins(engine, Session, ids, results, stock=1):
    assert sorted(v for v in results.values() if v != "ok") == [409] * (len(results) - stock), results
    assert list(results.values()).count("ok") == stock
    s = Session()
    tid, pid, wid = ids
    _, comp, libre = rsv.available(s, tid, pid, wid)
    assert comp == stock and libre == 0  # nunca saldo de reservas negativo
    s.close()


def test_ac06_concurrencia_sqlite_archivo():
    d = tempfile.mkdtemp(prefix="p1inv_")
    url = f"sqlite:///{os.path.join(d, 'race.db')}"
    engine, Session, ids = _setup_race_db(url)
    try:
        res = _race(Session, *ids)
        _assert_one_wins(engine, Session, ids, res)
    finally:
        engine.dispose()


def _pg_url():
    url = os.environ.get("TEST_PG_RACE_URL") or os.environ.get("TEST_DATABASE_URL")
    if url:
        return url.replace("postgresql://", "postgresql+psycopg://", 1), None
    pgserver = pytest.importorskip("pgserver", reason="sin Postgres: instalar pgserver o definir TEST_PG_RACE_URL")
    srv = pgserver.get_server(tempfile.mkdtemp(prefix="p1inv_pg_"), cleanup_mode="stop")
    return srv.get_uri().replace("postgresql://", "postgresql+psycopg://", 1), srv


def test_ac06_concurrencia_postgres_real():
    """Dos hilos, dos sesiones, misma ultima unidad: SELECT ... FOR UPDATE sobre stock_lock -> una gana, otra 409.
    Luego 8 hilos contra 3 unidades: exactamente 3 confirmadas."""
    pytest.importorskip("psycopg")
    url, srv = _pg_url()
    engine, Session, ids = _setup_race_db(url)
    try:
        res = _race(Session, *ids)
        _assert_one_wins(engine, Session, ids, res)
        s = Session()
        invsvc.move(s, ids[0], None, ids[1], ids[2], "entrada", Decimal(2))  # 3 en total, 1 ya comprometida
        s.commit()
        s.close()
        res8 = _race(Session, *ids, n_threads=8)
        assert list(res8.values()).count("ok") == 2 and list(res8.values()).count(409) == 6, res8
        s = Session()
        _, comp, libre = rsv.available(s, *ids)
        assert comp == 3 and libre == 0
        assert s.scalar(select(StockMovement.id).where(StockMovement.product_id == ids[1]).limit(1))
        s.close()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
        if srv is not None:
            srv.cleanup()
