"""bancos: saldo inicial con fecha de corte, caja, estados de cuenta importados y movimientos propios.

Reunion con la contadora (10/11/2026): cada cuenta arranca con su saldo a la fecha de corte y desde ahi se mueve con
cobros, gastos pagados, pagos a proveedor, planilla, comisiones y movimientos propios; lo registrado se concilia
contra el estado de cuenta del banco.

Aditiva, sin reescribir datos:
- bank_account.kind VARCHAR(8) NOT NULL DEFAULT 'banco' (banco | caja). opening_balance y opening_date ya existian.
- bank_statement_line.balance (saldo que reporta el banco) e import_id (de que archivo salio la linea).
- Tablas nuevas bank_statement_import y bank_movement.

Revision ID: c5e9a3f71d24
Revises: b7c4e1a9d302
Create Date: 2026-11-12 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c5e9a3f71d24"
down_revision: str | None = "c2f8a4d1e937"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bank_account", schema=None) as b:
        b.add_column(sa.Column("kind", sa.String(length=8), server_default="banco", nullable=False))

    op.create_table(
        "bank_statement_import",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("bank_account_id", sa.Integer(), sa.ForeignKey("bank_account.id", name="fk_bank_statement_import_account"), nullable=False),
        sa.Column("filename", sa.String(200), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("mapping", sa.JSON(), nullable=True),
        sa.Column("date_from", sa.Date(), nullable=True),
        sa.Column("date_to", sa.Date(), nullable=True),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("added", sa.Integer(), nullable=False),
        sa.Column("duplicates", sa.Integer(), nullable=False),
        sa.Column("closing_balance", sa.Numeric(16, 2), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_bank_statement_import_tenant_id", "bank_statement_import", ["tenant_id"])
    op.create_index("ix_bank_statement_import_bank_account_id", "bank_statement_import", ["bank_account_id"])

    op.create_table(
        "bank_movement",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("bank_account_id", sa.Integer(), sa.ForeignKey("bank_account.id", name="fk_bank_movement_account"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(16, 2), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("reference", sa.String(120), nullable=True),
        sa.Column("transfer_group", sa.String(40), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_bank_movement_tenant_id", "bank_movement", ["tenant_id"])
    op.create_index("ix_bank_movement_bank_account_id", "bank_movement", ["bank_account_id"])
    op.create_index("ix_bank_movement_date", "bank_movement", ["date"])
    op.create_index("ix_bank_movement_transfer_group", "bank_movement", ["transfer_group"])

    with op.batch_alter_table("bank_statement_line", schema=None) as b:
        b.add_column(sa.Column("balance", sa.Numeric(16, 2), nullable=True))
        b.add_column(sa.Column("import_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_bank_statement_line_import", "bank_statement_import", ["import_id"], ["id"], ondelete="SET NULL")
        b.create_index("ix_bank_statement_line_import_id", ["import_id"])


def downgrade() -> None:
    with op.batch_alter_table("bank_statement_line", schema=None) as b:
        b.drop_index("ix_bank_statement_line_import_id")
        b.drop_constraint("fk_bank_statement_line_import", type_="foreignkey")
        b.drop_column("import_id")
        b.drop_column("balance")
    op.drop_index("ix_bank_movement_transfer_group", table_name="bank_movement")
    op.drop_index("ix_bank_movement_date", table_name="bank_movement")
    op.drop_index("ix_bank_movement_bank_account_id", table_name="bank_movement")
    op.drop_index("ix_bank_movement_tenant_id", table_name="bank_movement")
    op.drop_table("bank_movement")
    op.drop_index("ix_bank_statement_import_bank_account_id", table_name="bank_statement_import")
    op.drop_index("ix_bank_statement_import_tenant_id", table_name="bank_statement_import")
    op.drop_table("bank_statement_import")
    with op.batch_alter_table("bank_account", schema=None) as b:
        b.drop_column("kind")
