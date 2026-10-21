"""salud: corridas de las tareas del worker (task_run)

Cada tarea del worker deja su corrida (inicio, fin, ok, resumen, error) por las señales de Celery, y el latido del
worker (cada 5 min) deja la suya: /health/deep y el correo diario de Salud salen de aqui. Tabla nueva de la
plataforma (sin empresa): nada existente cambia.

Revision ID: c033f3af9ca1
Revises: 31f46856365e
Create Date: 2026-10-20 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c033f3af9ca1"
down_revision: str | None = "31f46856365e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task", sa.String(80), nullable=False),
        sa.Column("task_id", sa.String(64), nullable=True, index=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_task_run_task_started", "task_run", ["task", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_task_run_task_started", table_name="task_run")
    op.drop_table("task_run")
