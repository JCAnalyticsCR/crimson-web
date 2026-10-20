import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.main import create_app
from app.seeds import seed

ADMIN = ("admin@crimsonapp.com", "Crimson-2026-seguro")


@pytest.fixture(autouse=True)
def _aislar_hacienda(monkeypatch):
    """Ninguna prueba sale a la red ni toma nada del .env del desarrollador: una llave FISCAL_KEYS aleatoria por prueba,
    ENV=local, sin credenciales de Hacienda ni buzon de entorno, produccion prohibida, modo fiscal "none", y un
    transporte HTTP que FALLA ante cualquier peticion (las pruebas que necesitan Hacienda instalan FakeHacienda)."""
    import base64

    import httpx
    from pydantic import SecretStr

    from app.core.config import settings
    from app.providers.hacienda import client as hclient

    monkeypatch.setattr(settings, "fiscal_keys", SecretStr("ktest:" + base64.urlsafe_b64encode(os.urandom(32)).decode()))
    monkeypatch.setattr(settings, "env", "local")
    monkeypatch.setattr(settings, "hacienda_production_allowed", False)
    monkeypatch.setattr(settings, "einvoice_provider", "none")
    monkeypatch.setattr(settings, "railway_environment_id", None)
    monkeypatch.setattr(settings, "hacienda_environment", "pruebas")
    for k in (
        "hacienda_username",
        "hacienda_password",
        "hacienda_p12_b64",
        "hacienda_p12_pin",
        "hacienda_env_tenant",
        "hacienda_proveedor_sistemas",
        "reception_imap_host",
        "reception_imap_user",
        "reception_imap_password",
        "reception_imap_tenant",
        "proxy_shared_key",
    ):
        monkeypatch.setattr(settings, k, None)
    hclient.reset_cache()

    def _bloqueado(request):
        raise AssertionError(f"Petición de red no simulada: {request.method} {request.url}")

    monkeypatch.setattr(hclient, "http_factory", lambda: httpx.Client(transport=httpx.MockTransport(_bloqueado)))
    yield
    hclient.reset_cache()


@pytest.fixture
def db_session():
    pg = os.environ.get("TEST_DATABASE_URL")  # opcional: correr la suite contra Postgres real
    if pg:
        engine = create_engine(pg.replace("postgresql://", "postgresql+psycopg://", 1))
        Base.metadata.drop_all(engine)
    else:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)

        @event.listens_for(engine, "connect")
        def _fk(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    seed(s, *ADMIN)
    yield s
    s.close()
    engine.dispose()


@pytest.fixture
def client(db_session):
    from app.core.ratelimit import (
        access_request_global_limiter,
        access_request_limiter,
        catalog_limiter,
        incident_global_limiter,
        incident_limiter,
        login_limiter,
        public_limiter,
        stock_alert_limiter,
    )

    login_limiter.reset()
    public_limiter.reset()
    incident_limiter.reset()
    incident_global_limiter.reset()
    access_request_limiter.reset()
    access_request_global_limiter.reset()
    catalog_limiter.reset()
    stock_alert_limiter.reset()
    from app.core import ratelimit as rl

    for lim in vars(rl).values():  # cualquier limitador nuevo tambien arranca limpio en cada prueba
        if isinstance(lim, rl.SlidingWindow):
            lim.reset()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


@pytest.fixture
def from_ip(monkeypatch):
    """Cabeceras del nginx del portal (plan 1.10) para que una peticion de prueba llegue con otra IP real.
    La llave es aleatoria por prueba: settings.proxy_shared_key solo existe mientras dura la prueba."""
    import secrets

    from pydantic import SecretStr

    from app.core.config import settings

    llave = secrets.token_urlsafe(32)
    monkeypatch.setattr(settings, "proxy_shared_key", SecretStr(llave))
    return lambda ip: {"X-Crimson-Client-IP": ip, "X-Crimson-Proxy-Key": llave}


@pytest.fixture
def auth(client):
    r = client.post("/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return r.json()


class FakeOnvo:
    """ONVO en memoria con la forma de la API real (sesiones de Checkout, intenciones, reembolsos). Sin red."""

    key = "onvo_test_secret_key_" + "k" * 24
    public = "onvo_test_publishable_key_" + "p" * 24
    wh = "webhook_secret_" + "w" * 24

    def __init__(self):
        self.sessions: dict[str, dict] = {}
        self.intents: dict[str, dict] = {}
        self.created: list[dict] = []
        self.refunds: list[dict] = []
        self.fail_get = 0  # cuantas consultas siguientes fallan (ONVO caido)

    def install(self, monkeypatch):
        from app.providers.payments import Intent, OnvoAdapter, OnvoError

        fake = self

        def create_checkout(self, **kw):
            sid = f"cs_{len(fake.sessions) + 1}"
            fake.created.append(kw)
            s = {"id": sid, "url": f"https://checkout.onvopay.com/pay/{sid}", "mode": "test", "status": "open", "paymentStatus": "unpaid",
                 "amountTotal": kw["amount_cents"], "currency": kw["currency"], "paymentIntentId": None,
                 "metadata": {k: str(v) for k, v in kw["metadata"].items()}}
            fake.sessions[sid] = s
            return Intent(id=sid, checkout_url=s["url"], status="unpaid", raw=s)

        def _get(store, oid):
            if fake.fail_get:
                fake.fail_get -= 1
                raise OnvoError(0, "sin respuesta de ONVO (ConnectTimeout)")
            if oid not in store:
                raise OnvoError(404, "not found")
            return dict(store[oid])

        def refund(self, intent_id, amount_cents=None, reason="requested_by_customer", description=None):
            r = {"id": f"rf_{len(fake.refunds) + 1}", "paymentIntentId": intent_id, "amount": amount_cents, "status": "succeeded"}
            fake.refunds.append(r)
            return r

        monkeypatch.setattr(OnvoAdapter, "create_checkout", create_checkout)
        monkeypatch.setattr(OnvoAdapter, "get_checkout", lambda self, sid: _get(fake.sessions, sid))
        monkeypatch.setattr(OnvoAdapter, "get_intent", lambda self, pid: _get(fake.intents, pid))
        monkeypatch.setattr(OnvoAdapter, "refund", refund)
        monkeypatch.setattr(OnvoAdapter, "ping", lambda self: {"data": []})
        return self

    def pay(self, sid: str, amount: int | None = None, intent_meta: dict | None = None) -> dict:
        """El comprador paga en ONVO: la sesion queda paid y nace su intencion succeeded."""
        s = self.sessions[sid]
        pid = f"pi_{sid}"
        s.update(paymentStatus="paid", status="complete", paymentIntentId=pid)
        if amount is not None:
            s["amountTotal"] = amount
        self.intents[pid] = {"id": pid, "status": "succeeded", "amount": s["amountTotal"], "currency": s["currency"], "metadata": intent_meta or {}}
        return s

    def connect(self, client, **extra):
        body = {"provider": "onvo", "client_id": self.public, "secret": self.key, "webhook_secret": self.wh, "active": True, "is_primary": True, **extra}
        r = client.put("/settings/gateways", json=body)
        assert r.status_code == 200, r.text

    def send(self, client, tid: int, typ: str, data: dict, secret: str | None = None):
        import json

        return client.post(
            f"/webhooks/onvo/{tid}",
            content=json.dumps({"type": typ, "data": data}).encode(),
            headers={"Content-Type": "application/json", "X-Webhook-Secret": self.wh if secret is None else secret},
        )


@pytest.fixture
def fake_onvo(monkeypatch):
    from app.services import onvo

    onvo._last_sync.clear()
    monkeypatch.setattr(onvo, "SYNC_EVERY", 0.0)  # el freno por sesion se prueba aparte (test_sync_throttle)
    return FakeOnvo().install(monkeypatch)


@pytest.fixture(scope="session")
def p12_empresa():
    """(.p12, PIN) autofirmado con la cedula ficticia de la empresa semilla. Una vez por corrida (RSA es lento)."""
    from tests.hacienda_utils import EMPRESA, make_p12

    return make_p12(EMPRESA)
