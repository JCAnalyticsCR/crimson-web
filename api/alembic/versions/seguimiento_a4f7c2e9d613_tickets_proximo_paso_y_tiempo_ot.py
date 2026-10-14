"""tickets y OT: proximo paso del ticket y registro de tiempo por jornada en la orden de trabajo

Columnas nuevas y nulas en support_ticket; tabla nueva work_order_time. Nada existente cambia: las ordenes
sin registro siguen calculando horas de inicio a fin.

Revision ID: a4f7c2e9d613
Revises: c5d18e3f7a2b
Create Date: 2026-10-14 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4f7c2e9d613"
down_revision: str | None = "c5d18e3f7a2b"  # otros frentes tambien encadenan aqui: quien integre ultimo reencadena
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("support_ticket") as b:
        b.add_column(sa.Column("next_step", sa.String(200), nullable=True))
        b.add_column(sa.Column("next_step_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "work_order_time",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("work_order_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("minutes", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(10), nullable=False, server_default="reloj"),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_work_order_time_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_order.id"], name="fk_work_order_time_order", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_work_order_time_user"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], name="fk_work_order_time_created_by"),
        sa.PrimaryKeyConstraint("id", name="pk_work_order_time"),
    )
    with op.batch_alter_table("work_order_time") as b:
        b.create_index("ix_work_order_time_tenant_id", ["tenant_id"])
        b.create_index("ix_work_order_time_work_order_id", ["work_order_id"])
        b.create_index("ix_work_order_time_user_id", ["user_id"])


def downgrade() -> None:
    with op.batch_alter_table("work_order_time") as b:
        b.drop_index("ix_work_order_time_user_id")
        b.drop_index("ix_work_order_time_work_order_id")
        b.drop_index("ix_work_order_time_tenant_id")
    op.drop_table("work_order_time")
    with op.batch_alter_table("support_ticket") as b:
        b.drop_column("next_step_at")
        b.drop_column("next_step")
