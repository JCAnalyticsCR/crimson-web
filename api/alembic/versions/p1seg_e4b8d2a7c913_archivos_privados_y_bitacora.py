"""seguridad P1: archivos publicos/privados y bitacora completa (antes/despues, motivo, version, contexto) inmutable

- media.visibility ('public' | 'private') y media.module (modulo de origen). Todo lo existente queda 'public'
  (server_default): ninguna URL que hoy funciona deja de funcionar.
- audit_log: before, after, reason, version, company (todo nullable; diff se mantiene) e indice por entidad.
- Postgres: trigger que rechaza UPDATE, DELETE y TRUNCATE sobre audit_log (solo se inserta). En SQLite no hay trigger.

Revision ID: e4b8d2a7c913
Revises: d0cb4e92f968
Create Date: 2026-10-12 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4b8d2a7c913"
down_revision: str | None = "d0cb4e92f968"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRIGGER_FN = """
CREATE OR REPLACE FUNCTION audit_log_inmutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log es inmutable: % no permitido', TG_OP USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    with op.batch_alter_table("media", schema=None) as batch_op:
        batch_op.add_column(sa.Column("visibility", sa.String(length=10), nullable=False, server_default="public"))
        batch_op.add_column(sa.Column("module", sa.String(length=40), nullable=True))
        batch_op.create_index("ix_media_visibility", ["visibility"], unique=False)

    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.add_column(sa.Column("before", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("after", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("reason", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("version", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("company", sa.String(length=160), nullable=True))
        batch_op.create_index("ix_audit_log_entity", ["entity"], unique=False)

    if op.get_bind().dialect.name == "postgresql":
        op.execute(TRIGGER_FN)
        op.execute("CREATE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION audit_log_inmutable()")
        op.execute("CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log FOR EACH STATEMENT EXECUTE FUNCTION audit_log_inmutable()")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log")
        op.execute("DROP TRIGGER IF EXISTS audit_log_no_update_delete ON audit_log")
        op.execute("DROP FUNCTION IF EXISTS audit_log_inmutable()")

    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.drop_index("ix_audit_log_entity")
        batch_op.drop_column("company")
        batch_op.drop_column("version")
        batch_op.drop_column("reason")
        batch_op.drop_column("after")
        batch_op.drop_column("before")

    with op.batch_alter_table("media", schema=None) as batch_op:
        batch_op.drop_index("ix_media_visibility")
        batch_op.drop_column("module")
        batch_op.drop_column("visibility")
