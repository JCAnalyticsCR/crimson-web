"""Engine, sesion y Base declarativa (SQLAlchemy 2.0)."""

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


def make_engine(url: str | None = None) -> Engine:
    url = url or settings.database_url
    # hide_parameters en produccion: el texto de un error de la base no lleva los valores de la fila (correos, cedulas,
    # el XML) a los logs, a task_run ni a los correos de falla (plan 1.8)
    kwargs: dict = {"pool_pre_ping": True, "hide_parameters": settings.is_prod}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):  # SQLite no aplica FKs por defecto
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
