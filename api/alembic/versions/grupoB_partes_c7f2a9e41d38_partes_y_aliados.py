"""grupo B: partes de la oportunidad (cliente final, sitio), aliados con solicitudes de costo y "aportado por"

Lo existente no cambia de significado: customer_id sigue siendo el contratante (a quien se cotiza y factura).
Las columnas nuevas nacen vacias, asi que toda oportunidad vieja queda con contratante = su cliente actual.

Revision ID: c7f2a9e41d38
Revises: e5a1c7d93b20
Create Date: 2026-10-06 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7f2a9e41d38"
down_revision: str | None = "e5a1c7d93b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("opportunity", schema=None) as batch_op:
        batch_op.add_column(sa.Column("end_customer_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("site", sa.String(length=300), nullable=True))
        batch_op.create_foreign_key("fk_opportunity_end_customer", "customer", ["end_customer_id"], ["id"])
        batch_op.create_index("ix_opportunity_end_customer_id", ["end_customer_id"], unique=False)

    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.add_column(sa.Column("end_customer_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_project_end_customer", "customer", ["end_customer_id"], ["id"])
        batch_op.create_index("ix_project_end_customer_id", ["end_customer_id"], unique=False)

    for table in ("quote_line", "invoice_line"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column("supplied_by", sa.String(length=160), nullable=True))

    op.create_table(
        "ally_participation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("supplier_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("scope_lines", sa.JSON(), nullable=False),
        sa.Column("contacts", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunity.id"], name="fk_ally_opportunity", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_ally_project", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["customer_id"], ["customer.id"], name="fk_ally_customer"),
        sa.ForeignKeyConstraint(["supplier_id"], ["supplier.id"], name="fk_ally_supplier"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], name="fk_ally_created_by"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_ally_participation_tenant", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_ally_participation"),
    )
    with op.batch_alter_table("ally_participation", schema=None) as batch_op:
        batch_op.create_index("ix_ally_participation_opportunity_id", ["opportunity_id"], unique=False)
        batch_op.create_index("ix_ally_participation_project_id", ["project_id"], unique=False)
        batch_op.create_index("ix_ally_participation_tenant_id", ["tenant_id"], unique=False)

    op.create_table(
        "ally_cost_request",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("participation_id", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("what", sa.String(length=300), nullable=False),
        sa.Column("responsible_id", sa.Integer(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("amount", sa.Numeric(precision=16, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("exclusions", sa.Text(), nullable=True),
        sa.Column("attachments", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["participation_id"], ["ally_participation.id"], name="fk_costreq_participation", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunity.id"], name="fk_costreq_opportunity", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["responsible_id"], ["user.id"], name="fk_costreq_responsible"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], name="fk_costreq_created_by"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_ally_cost_request_tenant", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_ally_cost_request"),
    )
    with op.batch_alter_table("ally_cost_request", schema=None) as batch_op:
        batch_op.create_index("ix_ally_cost_request_participation_id", ["participation_id"], unique=False)
        batch_op.create_index("ix_ally_cost_request_opportunity_id", ["opportunity_id"], unique=False)
        batch_op.create_index("ix_ally_cost_request_responsible_id", ["responsible_id"], unique=False)
        batch_op.create_index("ix_ally_cost_request_due_date", ["due_date"], unique=False)
        batch_op.create_index("ix_ally_cost_request_status", ["status"], unique=False)
        batch_op.create_index("ix_ally_cost_request_tenant_id", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_table("ally_cost_request")
    op.drop_table("ally_participation")

    for table in ("invoice_line", "quote_line"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("supplied_by")

    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.drop_index("ix_project_end_customer_id")
        batch_op.drop_constraint("fk_project_end_customer", type_="foreignkey")
        batch_op.drop_column("end_customer_id")

    with op.batch_alter_table("opportunity", schema=None) as batch_op:
        batch_op.drop_index("ix_opportunity_end_customer_id")
        batch_op.drop_constraint("fk_opportunity_end_customer", type_="foreignkey")
        batch_op.drop_column("site")
        batch_op.drop_column("end_customer_id")
