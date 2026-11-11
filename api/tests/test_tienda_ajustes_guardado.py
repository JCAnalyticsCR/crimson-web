"""O1-11b: los guardados de Mi Tienda no se pisan (PUT /store, /store/showcase y /store/checkout releen la fila de la
empresa con candado y cambian solo su llave), PUT /store deja bitacora con las cuentas enmascaradas y marca el
catalogo, y publicar desde el inventario web exige catalog.publicar. Datos ficticios."""

import threading

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Tenant
from tests.test_session4 import _user_with_role

METODOS = {
    "sinpe": {"active": True, "phone": "8888-7777", "holder": "Comercio de Prueba"},
    "transferencia": {"active": True, "iban": "CR05015201001026284066", "bank": "BCR", "holder": "Comercio de Prueba"},
}


def _otro_proceso_escribe(db_session, tid: int, **llaves):
    """Otro proceso (otra pestana, el worker) guarda en los ajustes de la tienda mientras esta sesion tiene la empresa
    en memoria: se escribe por otra sesion y esta NO se entera (expire_on_commit=False, como en la API)."""
    otra = Session(bind=db_session.get_bind())
    t = otra.get(Tenant, tid)
    st = dict(t.settings or {})
    st["store"] = {**(st.get("store") or {}), **llaves}
    t.settings = st
    otra.commit()
    otra.close()


@pytest.mark.parametrize("ruta", ["/store", "/store/showcase", "/store/checkout"])
def test_un_guardado_no_pisa_lo_que_otro_guardo_en_el_medio(client, auth, db_session, ruta):
    """La peticion carga la empresa (permisos), otro proceso guarda otra llave y recien entonces esta guarda la suya:
    las dos quedan. Se llama a la ruta con la empresa ya cargada para fijar ese orden sin hilos."""
    from types import SimpleNamespace as N

    from app.routers.store import StoreIn, put_store
    from app.routers.store_checkout import CheckoutCfgIn, put_checkout_cfg
    from app.routers.store_showcase import ShowcaseIn, showcase_put

    tid = auth["tenant"]["id"]
    assert client.put("/store", json={"kind": "tienda", "published": True}).status_code == 200
    t = db_session.get(Tenant, tid)  # la empresa en memoria, como la deja get_principal
    p = N(tenant=t, user=N(id=auth["user"]["id"]), ip=None)
    _otro_proceso_escribe(db_session, tid, tagline="Escrito por otra pestaña", showcase={"headline": "Titular de la otra pestaña"})
    if ruta == "/store":
        put_store(StoreIn(name="Tienda de prueba"), p, db_session)
    elif ruta == "/store/showcase":
        showcase_put(ShowcaseIn(subline="Bajada nueva"), p, db_session)
    else:
        put_checkout_cfg(CheckoutCfgIn(methods=METODOS), p, db_session)
    db_session.expire_all()
    store = db_session.get(Tenant, tid).settings["store"]
    assert store["tagline"] == "Escrito por otra pestaña"  # lo del otro sigue
    assert store["showcase"]["headline"] == "Titular de la otra pestaña"
    if ruta == "/store":
        assert store["name"] == "Tienda de prueba"
    elif ruta == "/store/showcase":
        assert store["showcase"]["subline"] == "Bajada nueva"
    else:
        assert store["checkout"]["methods"]["sinpe"]["active"] is True


def test_put_store_deja_bitacora_sin_numeros_de_cuenta_y_marca_el_catalogo(client, auth, db_session):
    tid = auth["tenant"]["id"]
    v0 = (db_session.get(Tenant, tid).settings or {}).get("catalog_v") or 0
    r = client.put(
        "/store",
        json={
            "tagline": "Pagos al SINPE 8888-7777 o a la cuenta CR05015201001026284066",
            "whatsapp": "+50688889892",
            "business": {"phone": "2222-3333", "hours": "Lunes a viernes"},
            "legal": {"terms": "Términos de prueba"},
        },
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert (db_session.get(Tenant, tid).settings.get("catalog_v") or 0) > v0  # la vitrina y el snapshot ven lo nuevo
    log = db_session.scalar(select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.entity == "store").order_by(AuditLog.id.desc()))
    assert log is not None and set(log.diff["campos"]) == {"tagline", "whatsapp", "business", "legal.terms"}
    texto = str(log.before) + str(log.after) + str(log.diff)
    for numero in ("88887777", "8888-7777", "015201001026284066", "50688889892", "2222-3333"):
        assert numero not in texto, numero
    assert "••••7777" in texto and "CR••••4066" in texto and log.after["legal.terms"] == "18 caracteres"
    # lo que no cambia no se anota otra vez
    n = db_session.scalar(select(AuditLog.id).where(AuditLog.entity == "store").order_by(AuditLog.id.desc()))
    client.put("/store", json={"whatsapp": "+50688889892"})
    assert db_session.scalar(select(AuditLog.id).where(AuditLog.entity == "store").order_by(AuditLog.id.desc())) == n


def test_publicar_desde_el_inventario_web_exige_catalog_publicar(client, auth, db_session):
    pr = client.post("/products", json={"name": "Equipo de prueba", "code": "WEB-INV-1", "price": 1500}).json()
    pw = _user_with_role(db_session, auth["tenant"]["id"], "inventario.web2@ejemplo.com", "inventario")
    keep = client.headers.pop("Authorization")
    tok = client.post("/auth/login", json={"email": "inventario.web2@ejemplo.com", "password": pw}).json()["access_token"]
    client.headers["Authorization"] = keep
    h = {"Authorization": f"Bearer {tok}"}
    url = f"/store/inventory/products/{pr['id']}"
    assert client.patch(url, json={"show_on_web": True}, headers=h).status_code == 403
    # precio web y destacado si (mandando la publicacion como esta, como hace el panel)
    r = client.patch(url, json={"show_on_web": False, "web_featured": True, "web_price": "1200"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["show_on_web"] is False and r.json()["web_featured"] is True
    assert client.patch(url, json={"show_on_web": True}).json()["show_on_web"] is True  # el administrador si
    assert client.patch(url, json={"show_on_web": False}, headers=h).status_code == 403  # despublicar tampoco


# ---------------------------------------------------------------- Postgres real (CI): dos guardados a la vez


def test_dos_guardados_simultaneos_postgres_real():
    """Dos hilos, dos sesiones: PUT /store (frase) y PUT /store/showcase (titular) a la vez, 6 rondas. Con SELECT ...
    FOR UPDATE de la fila de la empresa los dos cambios quedan siempre; sin el, uno pisaba al otro."""
    pytest.importorskip("psycopg")
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.db import Base, get_db
    from app.main import create_app
    from app.seeds import seed
    from tests.conftest import ADMIN
    from tests.test_inventario_reservas import _pg_url

    url, srv = _pg_url()
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    Mk = sessionmaker(bind=engine, expire_on_commit=False)
    s = Mk()
    seed(s, *ADMIN)
    s.close()
    try:

        def db():
            ss = Mk()
            try:
                yield ss
            finally:
                ss.close()

        app = create_app()
        app.dependency_overrides[get_db] = db
        c = TestClient(app)
        tok = c.post("/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}).json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        assert c.put("/store", json={"kind": "tienda"}, headers=h).status_code == 200
        for i in range(6):
            go = threading.Barrier(2)
            res: dict[str, int] = {}

            def put(ruta, body, i=i, go=go, res=res):
                go.wait()
                res[ruta] = c.put(ruta, json=body, headers=h).status_code

            hilos = [
                threading.Thread(target=put, args=("/store", {"tagline": f"frase {i}"})),
                threading.Thread(target=put, args=("/store/showcase", {"headline": f"titular {i}"})),
            ]
            for x in hilos:
                x.start()
            for x in hilos:
                x.join()
            assert res == {"/store": 200, "/store/showcase": 200}, res
            ss = Mk()
            store = ss.scalar(select(Tenant).where(Tenant.slug == "crimson")).settings["store"]
            ss.close()
            assert store["tagline"] == f"frase {i}" and store["showcase"]["headline"] == f"titular {i}", (i, store)
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
        if srv is not None:
            srv.cleanup()
