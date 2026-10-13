"""p1 vehiculos: flota, cargas de combustible (con exclusion del analisis) y otros costos del vehiculo

Revision ID: 9d4e2b7a1c36
Revises: d5a8c3e17f42
Create Date: 2026-10-12 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9d4e2b7a1c36"
down_revision: str | None = "d5a8c3e17f42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "vehicle",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("plate", sa.String(length=20), nullable=True),
        sa.Column("kind", sa.String(length=30), nullable=True),
        sa.Column("owner", sa.String(length=10), nullable=False, server_default="empresa"),
        sa.Column("driver", sa.String(length=120), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("excluded_from_analysis", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("exclusion_reason", sa.String(length=240), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_ts(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_vehicle_tenant", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_vehicle"),
    )
    with op.batch_alter_table("vehicle", schema=None) as batch_op:
        batch_op.create_index("ix_vehicle_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_index("ix_vehicle_plate", ["plate"], unique=False)

    op.create_table(
        "fuel_load",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("driver", sa.String(length=120), nullable=True),
        sa.Column("liters", sa.Numeric(12, 3), nullable=True),
        sa.Column("amount", sa.Numeric(16, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="CRC"),
        sa.Column("odometer", sa.Integer(), nullable=True),
        sa.Column("full_tank", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("receipt_media_id", sa.Integer(), nullable=True),
        sa.Column("usage", sa.String(length=160), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("excluded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("exclusion_reason", sa.String(length=240), nullable=True),
        sa.Column("source", sa.String(length=12), nullable=False, server_default="manual"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        *_ts(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_fuel_load_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicle.id"], name="fk_fuel_load_vehicle", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["receipt_media_id"], ["media.id"], name="fk_fuel_load_media", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_fuel_load_project", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], name="fk_fuel_load_user"),
        sa.PrimaryKeyConstraint("id", name="pk_fuel_load"),
    )
    with op.batch_alter_table("fuel_load", schema=None) as batch_op:
        batch_op.create_index("ix_fuel_load_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_index("ix_fuel_load_vehicle_id", ["vehicle_id"], unique=False)
        batch_op.create_index("ix_fuel_load_date", ["date"], unique=False)
        batch_op.create_index("ix_fuel_load_project_id", ["project_id"], unique=False)

    op.create_table(
        "vehicle_cost",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Numeric(16, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="CRC"),
        sa.Column("description", sa.String(length=240), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        *_ts(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_vehicle_cost_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicle.id"], name="fk_vehicle_cost_vehicle", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], name="fk_vehicle_cost_user"),
        sa.PrimaryKeyConstraint("id", name="pk_vehicle_cost"),
    )
    with op.batch_alter_table("vehicle_cost", schema=None) as batch_op:
        batch_op.create_index("ix_vehicle_cost_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_index("ix_vehicle_cost_vehicle_id", ["vehicle_id"], unique=False)
        batch_op.create_index("ix_vehicle_cost_date", ["date"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("vehicle_cost", schema=None) as batch_op:
        batch_op.drop_index("ix_vehicle_cost_date")
        batch_op.drop_index("ix_vehicle_cost_vehicle_id")
        batch_op.drop_index("ix_vehicle_cost_tenant_id")
    op.drop_table("vehicle_cost")
    with op.batch_alter_table("fuel_load", schema=None) as batch_op:
        batch_op.drop_index("ix_fuel_load_project_id")
        batch_op.drop_index("ix_fuel_load_date")
        batch_op.drop_index("ix_fuel_load_vehicle_id")
        batch_op.drop_index("ix_fuel_load_tenant_id")
    op.drop_table("fuel_load")
    with op.batch_alter_table("vehicle", schema=None) as batch_op:
        batch_op.drop_index("ix_vehicle_plate")
        batch_op.drop_index("ix_vehicle_tenant_id")
    op.drop_table("vehicle")
