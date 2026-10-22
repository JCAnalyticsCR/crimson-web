"""cuentas por pagar: pagos a proveedor, aplicaciones y extension de conciliacion bancaria

Revision ID: a9f2c3d4e5b6
Revises: f3a8b1c92d45
Create Date: 2026-10-22

Tablas nuevas
  supplier_payment       -- pago a proveedor (empresa, proveedor, fecha, monto, metodo, etc.)
  supplier_payment_application -- monto aplicado a cada gasto de credito

Cambio en tabla existente
  bank_statement_line.matched_type  String(10) -> String(20)
  para que quepa "supplier_payment" (16 caracteres)

Down: elimina las tablas y revierte la columna a 10.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a9f2c3d4e5b6"
down_revision = "f3a8b1c92d45"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- supplier_payment -------------------------------------------------------
    op.create_table(
        "supplier_payment",
        sa.Column("id", sa.Integer(), nullable=False),
        # TenantMixin: ondelete=RESTRICT, index=True → ix_supplier_payment_tenant_id
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("supplier.id"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(16, 5), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CRC"),
        sa.Column("fx_rate", sa.Numeric(12, 5), nullable=False, server_default="1"),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("bank_account_id", sa.Integer(), sa.ForeignKey("bank_account.id"), nullable=True),
        sa.Column("reference", sa.String(120), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="registrado"),
        sa.Column("void_reason", sa.String(300), nullable=True),
        sa.Column(
            "voided_by",
            sa.Integer(),
            sa.ForeignKey("user.id", name="fk_supplier_payment_voided_by"),
            nullable=True,
        ),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("user.id", name="fk_supplier_payment_created_by"),
            nullable=True,
        ),
        # TimestampMixin: nullable=False
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # TenantMixin index=True → ix_supplier_payment_tenant_id
    op.create_index("ix_supplier_payment_tenant_id", "supplier_payment", ["tenant_id"])
    # supplier_id index=True → ix_supplier_payment_supplier_id
    op.create_index("ix_supplier_payment_supplier_id", "supplier_payment", ["supplier_id"])
    # date index=True → ix_supplier_payment_date
    op.create_index("ix_supplier_payment_date", "supplier_payment", ["date"])

    # --- supplier_payment_application -------------------------------------------
    op.create_table(
        "supplier_payment_application",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "payment_id",
            sa.Integer(),
            sa.ForeignKey("supplier_payment.id", ondelete="CASCADE", name="fk_spa_payment"),
            nullable=False,
        ),
        sa.Column(
            "expense_id",
            sa.Integer(),
            sa.ForeignKey("expense.id", name="fk_spa_expense"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(16, 5), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # payment_id index=True → ix_supplier_payment_application_payment_id
    op.create_index("ix_supplier_payment_application_payment_id", "supplier_payment_application", ["payment_id"])
    # expense_id index=True → ix_supplier_payment_application_expense_id
    op.create_index("ix_supplier_payment_application_expense_id", "supplier_payment_application", ["expense_id"])

    # --- ampliar matched_type de String(10) a String(20) ------------------------
    with op.batch_alter_table("bank_statement_line") as batch_op:
        batch_op.alter_column("matched_type", type_=sa.String(20), existing_nullable=True)


def downgrade() -> None:
    # truncar valores que no caben antes de reducir la columna
    op.execute("UPDATE bank_statement_line SET matched_type = NULL WHERE matched_type = 'supplier_payment'")
    with op.batch_alter_table("bank_statement_line") as batch_op:
        batch_op.alter_column("matched_type", type_=sa.String(10), existing_nullable=True)

    op.drop_index("ix_supplier_payment_application_expense_id", "supplier_payment_application")
    op.drop_index("ix_supplier_payment_application_payment_id", "supplier_payment_application")
    op.drop_table("supplier_payment_application")

    op.drop_index("ix_supplier_payment_date", "supplier_payment")
    op.drop_index("ix_supplier_payment_supplier_id", "supplier_payment")
    op.drop_index("ix_supplier_payment_tenant_id", "supplier_payment")
    op.drop_table("supplier_payment")
