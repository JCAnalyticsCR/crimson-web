"""CI (plan 1.1): una sola cabeza de Alembic, la cadena completa corre sobre una base vacia, el modelo y las migraciones
dicen lo mismo (lo que en el CI hace `alembic check` sobre Postgres) y la deriva de warranty_case.history se corrige."""

import warnings
from pathlib import Path

import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from alembic import command
from app.core.db import Base, make_engine

API = Path(__file__).resolve().parents[1]


def _config() -> Config:
    cfg = Config()  # sin alembic.ini: env.py no reconfigura el logging de las demas pruebas
    cfg.set_main_option("script_location", str(API / "alembic"))
    return cfg


def test_una_sola_cabeza():
    heads = ScriptDirectory.from_config(_config()).get_heads()
    assert len(heads) == 1, f"varias cabezas de Alembic: {heads} (re-encadenar down_revision al head vigente)"


def test_cadena_completa_sin_deriva_y_historial_de_garantias(tmp_path, monkeypatch):
    from app.core.config import settings

    url = f"sqlite:///{(tmp_path / 'migraciones.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url)
    cfg = _config()
    command.upgrade(cfg, "d7e2a9c41f58")  # garantias: history nacio nula
    eng = make_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("PRAGMA foreign_keys=OFF"))
        c.execute(
            sa.text(
                "INSERT INTO warranty_case (id, tenant_id, number, customer_id, equipment, quantity, status, replacement_qty, history, created_at, updated_at)"
                " VALUES (1, 1, 'GAR-1', 1, 'Equipo de prueba', 1, 'recibido', 0, NULL, '2026-10-19 12:00:00', '2026-10-19 12:00:00')"
            )
        )
    eng.dispose()

    command.upgrade(cfg, "head")
    eng = make_engine(url)
    with eng.connect() as c:
        assert c.scalar(sa.text("SELECT history FROM warranty_case WHERE id = 1")) == "[]"
        col = next(x for x in sa.inspect(c).get_columns("warranty_case") if x["name"] == "history")
        assert col["nullable"] is False
        with warnings.catch_warnings():  # el indice por expresion de aliados no se puede comparar en SQLite (si en Postgres)
            warnings.simplefilter("ignore")
            diffs = compare_metadata(MigrationContext.configure(c, opts={"render_as_batch": True}), Base.metadata)
    eng.dispose()
    assert diffs == [], f"el modelo y las migraciones no coinciden: {diffs}"

    command.downgrade(cfg, "d7e2a9c41f58")  # reversible (lo de despues tambien tiene que bajar)
    eng = make_engine(url)
    with eng.connect() as c:
        col = next(x for x in sa.inspect(c).get_columns("warranty_case") if x["name"] == "history")
        assert col["nullable"] is True
    eng.dispose()
    command.upgrade(cfg, "head")
