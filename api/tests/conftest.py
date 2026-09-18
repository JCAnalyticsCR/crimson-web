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
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


@pytest.fixture
def auth(client):
    r = client.post("/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return r.json()
