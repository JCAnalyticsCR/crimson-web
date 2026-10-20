"""Plan 1.10 · CORS: cookies solo para el portal; la landing sin cookies y solo en sus dos rutas publicas; el refresh
de la sesion solo desde el portal (Origin y Sec-Fetch-Site)."""

import pytest
from fastapi.testclient import TestClient

from tests.conftest import ADMIN

PANEL = "https://panel.ejemplo-crimson.test"
LANDING = "https://ejemplo-crimson.test"


@pytest.fixture
def c(db_session, monkeypatch):
    from app.core.config import settings
    from app.core.db import get_db
    from app.main import create_app

    monkeypatch.setattr(settings, "cors_origins", [PANEL])
    monkeypatch.setattr(settings, "landing_origins", [LANDING, "https://www.ejemplo-crimson.test"])
    monkeypatch.setattr(settings, "public_base_url", PANEL)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _pre(c, path, origin, method="POST"):
    return c.options(path, headers={"Origin": origin, "Access-Control-Request-Method": method, "Access-Control-Request-Headers": "content-type"})


def test_la_landing_no_recibe_credenciales(c):
    for path in ("/public/incidents", "/public/access-requests"):
        r = _pre(c, path, LANDING)
        assert r.status_code == 200 and r.headers["access-control-allow-origin"] == LANDING
        assert "access-control-allow-credentials" not in r.headers
    # la respuesta real tampoco
    r = c.post("/public/incidents", json={}, headers={"Origin": LANDING})
    assert r.headers.get("access-control-allow-origin") == LANDING and "access-control-allow-credentials" not in r.headers


def test_la_landing_no_tiene_cors_en_otras_rutas(c):
    for path, m in (("/auth/me", "GET"), ("/auth/refresh", "POST"), ("/customers", "GET"), ("/public/sla", "GET")):
        r = _pre(c, path, LANDING, m)
        assert "access-control-allow-origin" not in r.headers, path
    r = c.get("/health", headers={"Origin": LANDING})
    assert "access-control-allow-origin" not in r.headers


def test_el_portal_si_con_cookies(c):
    r = _pre(c, "/auth/refresh", PANEL)
    assert r.headers["access-control-allow-origin"] == PANEL and r.headers["access-control-allow-credentials"] == "true"
    r = _pre(c, "/public/incidents", PANEL)
    assert r.headers["access-control-allow-origin"] == PANEL


def test_otro_origen_nada(c):
    r = _pre(c, "/public/incidents", "https://malo.example")
    assert r.headers.get("access-control-allow-origin") != "https://malo.example"


def test_refresh_solo_desde_el_portal(c):
    assert c.post("/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}).status_code == 200
    # otro origen o una peticion de otro sitio: no se canjea la cookie (y no la rota)
    assert c.post("/auth/refresh", headers={"Origin": LANDING}).status_code == 403
    assert c.post("/auth/refresh", headers={"Origin": "null"}).status_code == 403
    assert c.post("/auth/refresh", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    # desde el portal (o sin Origin, mismo origen), si
    assert c.post("/auth/refresh", headers={"Origin": PANEL, "Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert c.post("/auth/refresh", headers={"Origin": PANEL + "/"}).status_code == 200
    assert c.post("/auth/refresh").status_code == 200
