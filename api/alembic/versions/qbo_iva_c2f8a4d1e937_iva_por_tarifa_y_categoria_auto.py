"""IVA por tarifa en gastos y categoria automatica por proveedor (reunion con la contadora, 10/11/2026).

Tablas nuevas
  expense_tax_line        -- desglose del gasto por (tipo, tarifa): base, IVA, exonerado, credito, prorrata
  supplier_category_rule  -- proveedor -> categoria (aprendida de las correcciones o fijada a mano)

Cambio en tabla existente
  expense.category_auto BOOLEAN NOT NULL DEFAULT false -- la categoria la puso la regla del proveedor.
  Agregar una columna con default constante no reescribe la tabla en Postgres 11+.

Aditiva: no toca montos. El desglose de los gastos que ya existen lo llena `python -m app.tools.desglose_iva_gastos`
(simula por defecto; --aplicar escribe). Bajar quita las dos tablas y la columna.

Revision ID: c2f8a4d1e937
Revises: b7c4e1a9d302
Create Date: 2026-11-12 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c2f8a4d1e937"
down_revision: str | None = "b7c4e1a9d302"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "expense_tax_line",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("expense_id", sa.Integer(), sa.ForeignKey("expense.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("kind", sa.String(16), nullable=False, server_default="gravado"),
        sa.Column("rate", sa.Numeric(6, 3), nullable=False, server_default="0"),
        sa.Column("rate_code", sa.String(2), nullable=True),
        sa.Column("base", sa.Numeric(16, 5), nullable=False, server_default="0"),
        sa.Column("tax", sa.Numeric(16, 5), nullable=False, server_default="0"),
        sa.Column("exonerated", sa.Numeric(16, 5), nullable=False, server_default="0"),
        sa.Column("credit", sa.Numeric(16, 5), nullable=False, server_default="0"),
        sa.Column("prorrata", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CRC"),
        sa.Column("source", sa.String(8), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_expense_tax_line_tenant_id", "expense_tax_line", ["tenant_id"])
    op.create_index("ix_expense_tax_line_expense_id", "expense_tax_line", ["expense_id"])

    op.create_table(
        "supplier_category_rule",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("supplier.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("expense_category.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(10), nullable=False, server_default="aprendida"),
        sa.Column("hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "supplier_id", name="uq_supplier_category_rule"),
    )
    op.create_index("ix_supplier_category_rule_tenant_id", "supplier_category_rule", ["tenant_id"])
    op.create_index("ix_supplier_category_rule_supplier_id", "supplier_category_rule", ["supplier_id"])

    with op.batch_alter_table("expense", schema=None) as b:
        b.add_column(sa.Column("category_auto", sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("expense", schema=None) as b:
        b.drop_column("category_auto")
    op.drop_index("ix_supplier_category_rule_supplier_id", table_name="supplier_category_rule")
    op.drop_index("ix_supplier_category_rule_tenant_id", table_name="supplier_category_rule")
    op.drop_table("supplier_category_rule")
    op.drop_index("ix_expense_tax_line_expense_id", table_name="expense_tax_line")
    op.drop_index("ix_expense_tax_line_tenant_id", table_name="expense_tax_line")
    op.drop_table("expense_tax_line")
