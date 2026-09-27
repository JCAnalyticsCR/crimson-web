"""archivo y papelera: levantamientos, proyectos, oportunidades y ordenes de trabajo

Revision ID: a7d2c9e41f60
Revises: c4a1f0e2b7d3
Create Date: 2026-09-26

Solo columnas nulas: las filas existentes quedan activas (ni archivadas ni en papelera) sin backfill.
"""

import sqlalchemy as sa
from alembic import op

revision = "a7d2c9e41f60"
down_revision = "c4a1f0e2b7d3"
branch_labels = None
depends_on = None

TABLES = ("opportunity", "survey", "project", "work_order")


def upgrade() -> None:
    for t in TABLES:
        with op.batch_alter_table(t, schema=None) as batch_op:
            batch_op.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
            batch_op.add_column(sa.Column("archived_by", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True))
            batch_op.add_column(sa.Column("trashed_by", sa.Integer(), nullable=True))
            batch_op.create_index(batch_op.f(f"ix_{t}_archived_at"), ["archived_at"], unique=False)
            batch_op.create_index(batch_op.f(f"ix_{t}_trashed_at"), ["trashed_at"], unique=False)


def downgrade() -> None:
    for t in reversed(TABLES):
        with op.batch_alter_table(t, schema=None) as batch_op:
            batch_op.drop_index(batch_op.f(f"ix_{t}_trashed_at"))
            batch_op.drop_index(batch_op.f(f"ix_{t}_archived_at"))
            batch_op.drop_column("trashed_by")
            batch_op.drop_column("trashed_at")
            batch_op.drop_column("archived_by")
            batch_op.drop_column("archived_at")
