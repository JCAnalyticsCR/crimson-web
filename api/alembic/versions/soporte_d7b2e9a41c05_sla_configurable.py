"""soporte: SLA configurable (contrato > empresa > defecto), plazo de resolucion y foto del SLA en el ticket

Revision ID: d7b2e9a41c05
Revises: c4a1f0e2b7d3
Create Date: 2026-09-26 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7b2e9a41c05"
down_revision: str | None = "c4a1f0e2b7d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # todo nullable: los tickets viejos quedan "sin SLA registrado" y se recalculan al editarlos
    with op.batch_alter_table("support_ticket", schema=None) as batch_op:
        batch_op.add_column(sa.Column("resolve_due_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("sla", sa.JSON(), nullable=True))

    with op.batch_alter_table("maintenance_contract", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sla", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("maintenance_contract", schema=None) as batch_op:
        batch_op.drop_column("sla")

    with op.batch_alter_table("support_ticket", schema=None) as batch_op:
        batch_op.drop_column("sla")
        batch_op.drop_column("resolve_due_at")
