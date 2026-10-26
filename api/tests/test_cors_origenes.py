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


def test_landing_en_cors_origins_igual_va_sin_cookies(db_session, monkeypatch):
    """La landing que quedo en CORS_ORIGINS de antes (o un "*") no recibe cookies: con cookies solo el panel."""
    from app.core.config import settings
    from app.core.db import get_db
    from app.main import create_app

    monkeypatch.setattr(settings, "cors_origins", [PANEL, LANDING, "*"])
    monkeypatch.setattr(settings, "landing_origins", [LANDING + "/"])
    monkeypatch.setattr(settings, "public_base_url", PANEL)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    c2 = TestClient(app)
    r = _pre(c2, "/public/incidents", LANDING)
    assert r.headers["access-control-allow-origin"] == LANDING and "access-control-allow-credentials" not in r.headers
    assert "access-control-allow-origin" not in _pre(c2, "/auth/refresh", LANDING).headers
    otro = _pre(c2, "/auth/refresh", "https://malo.example")
    assert otro.headers.get("access-control-allow-origin") not in ("*", "https://malo.example")
    assert _pre(c2, "/auth/refresh", PANEL).headers["access-control-allow-credentials"] == "true"
    # el refresh tampoco se canjea desde la landing
    assert c2.post("/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}).status_code == 200
    assert c2.post("/auth/refresh", headers={"Origin": LANDING}).status_code == 403


def test_en_produccion_solo_el_panel_con_cookies(db_session, monkeypatch, caplog):
    """La tienda es del mismo sitio que el panel (la cookie de sesion viaja en sus peticiones): aunque figure en
    CORS_ORIGINS, en produccion va sin cookies y no canjea el refresh. Con cookies solo el origen de PUBLIC_BASE_URL."""
    import logging

    from app.core.config import settings
    from app.core.db import get_db
    from app.main import create_app

    tienda = "https://tienda.ejemplo-crimson.test"
    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(settings, "cors_origins", [PANEL, tienda])
    monkeypatch.setattr(settings, "landing_origins", [LANDING])
    monkeypatch.setattr(settings, "public_base_url", PANEL + "/")
    with caplog.at_level(logging.ERROR, logger="crimson.config"):
        app = create_app()
    assert any(tienda in r.getMessage() and r.levelno == logging.ERROR for r in caplog.records)
    app.dependency_overrides[get_db] = lambda: db_session
    c3 = TestClient(app)
    r = _pre(c3, "/auth/refresh", tienda)
    assert r.headers.get("access-control-allow-origin") == tienda and "access-control-allow-credentials" not in r.headers
    r = c3.get("/health", headers={"Origin": tienda})
    assert "access-control-allow-credentials" not in r.headers
    assert _pre(c3, "/auth/refresh", PANEL).headers["access-control-allow-credentials"] == "true"
    # el refresh solo desde el panel: la tienda (Sec-Fetch-Site same-site) no se lleva un token
    assert c3.post("/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}).status_code == 200
    assert c3.post("/auth/refresh", headers={"Origin": tienda, "Sec-Fetch-Site": "same-site"}).status_code == 403
    assert c3.post("/auth/refresh", headers={"Origin": PANEL, "Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_en_produccion_el_panel_con_cookies_aunque_no_figure_en_cors_origins(db_session, monkeypatch):
    from app.core.cors import split_origins

    o = split_origins(["https://otro.ejemplo-crimson.test", "*"], [LANDING], PANEL, prod=True)
    assert o.credentials == (PANEL,) and o.plain == ("https://otro.ejemplo-crimson.test",) and o.ignored == ("*",)
    # fuera de produccion, todos los de CORS_ORIGINS con cookies (desarrollo local)
    o = split_origins([PANEL, "HTTP://localhost:5173/"], [LANDING], PANEL, prod=False)
    assert o.credentials == (PANEL, "http://localhost:5173") and o.plain == ()


def test_la_landing_lee_la_ficha_publica_de_un_producto(c):
    """26/10: los destacados de la portada muestran el precio vigente: GET de la ficha publica, sin cookies. Nada mas."""
    r = c.get("/public/store/crimson/products/152", headers={"Origin": LANDING})
    assert r.headers.get("access-control-allow-origin") == LANDING and "access-control-allow-credentials" not in r.headers
    for path, m in (("/public/store/crimson/products/152", "POST"), ("/public/store/crimson/products", "GET"), ("/public/store/crimson/checkout-config", "GET")):
        r = _pre(c, path, LANDING, m) if m != "GET" else c.get(path, headers={"Origin": LANDING})
        assert "access-control-allow-origin" not in r.headers, (path, m)
