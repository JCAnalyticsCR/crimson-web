"""aliados: bitacora compartida con el aliado y monto de la cotizacion (reunion 2, 21/10, pedidos 14 y 15)

`ally_shared_note`: huella (sha256) de cada entrada de la bitacora de una oportunidad que Crimson marca "Visible
para el aliado". Por defecto nada se comparte: sin relleno.

`ally_company.show_quote_total`: si el aliado ve el monto total de la cotizacion en el detalle de la oportunidad en
comun (pregunta abierta 8). Por defecto NO (false para todas las empresas que ya existen).

downgrade borra la tabla y la columna (lo compartido vuelve a quedar oculto).

Revision ID: c5d2e8f41a76
Revises: f3a8d2c6b917
Create Date: 2026-10-22 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c5d2e8f41a76"
down_revision: str | None = "f3a8d2c6b917"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ally_company") as b:
        b.add_column(sa.Column("show_quote_total", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "ally_shared_note",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("line_key", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunity.id"], name="fk_ally_shared_note_opportunity", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], name="fk_ally_shared_note_created_by"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("opportunity_id", "line_key", name="uq_ally_shared_note"),
    )
    with op.batch_alter_table("ally_shared_note") as b:
        b.create_index("ix_ally_shared_note_tenant_id", ["tenant_id"], unique=False)
        b.create_index("ix_ally_shared_note_opportunity_id", ["opportunity_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("ally_shared_note") as b:
        b.drop_index("ix_ally_shared_note_opportunity_id")
        b.drop_index("ix_ally_shared_note_tenant_id")
    op.drop_table("ally_shared_note")
    with op.batch_alter_table("ally_company") as b:
        b.drop_column("show_quote_total")
