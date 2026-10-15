from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.core.db import Base
from app.models import *  # noqa: F401,F403  (registra todos los modelos)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=settings.database_url, target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        if connection.dialect.name == "postgresql":
            # Las migraciones van en UNA transaccion y toman ACCESS EXCLUSIVE sobre tablas que el api viejo sigue usando
            # durante el despliegue. Sin tope, un ALTER que espera una transaccion abierta (p. ej. el worker viejo) deja
            # en cola todo el api. Con 15 s falla rapido: el contenedor nuevo no arranca, el viejo sigue sirviendo y el
            # despliegue se reintenta. SET de sesion + commit: sigue vigente en la transaccion de alembic.
            connection.exec_driver_sql("SET lock_timeout = '15s'")
            connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=connection.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
