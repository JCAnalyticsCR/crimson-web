"""asientos de diario para exportar a QuickBooks Online

Revision ID: 8d3f2e1a4c70
Revises: f3a8b1c92d45
Create Date: 2026-10-22

Tablas nuevas:
  qbo_account             – plan de cuentas importado de QBO
  accounting_role_mapping – rol contable → QboAccount
  accounting_period       – control de cierre mensual
  accounting_export       – bitácora de exportaciones
  journal_entry           – asiento por documento
  journal_line            – línea de debe/haber

Reversible: downgrade() borra todas las tablas en orden inverso.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "8d3f2e1a4c70"
down_revision = "f3a8b1c92d45"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # qbo_account                                                          #
    # ------------------------------------------------------------------ #
    op.create_table(
        "qbo_account",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("full_name", sa.String(300), nullable=False),
        sa.Column("number", sa.String(40)),
        sa.Column("account_type", sa.String(80)),
        sa.Column("detail_type", sa.String(80)),
        sa.Column("description", sa.String(300)),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CRC"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("import_batch", sa.String(40)),
        sa.UniqueConstraint("tenant_id", "full_name", name="uq_qbo_account_name"),
    )
    op.create_index("ix_qbo_account_tenant", "qbo_account", ["tenant_id"])

    # ------------------------------------------------------------------ #
    # accounting_role_mapping                                              #
    # ------------------------------------------------------------------ #
    op.create_table(
        "accounting_role_mapping",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("role_key", sa.String(80), nullable=False),
        sa.Column("qbo_account_id", sa.Integer(),
                  sa.ForeignKey("qbo_account.id", ondelete="SET NULL", name="fk_arm_qbo")),
        sa.Column("updated_by", sa.Integer()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "role_key", name="uq_arm_role"),
    )

    # ------------------------------------------------------------------ #
    # accounting_period                                                    #
    # ------------------------------------------------------------------ #
    op.create_table(
        "accounting_period",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="abierto"),
        sa.Column("exported_at", sa.DateTime(timezone=True)),
        sa.Column("exported_by", sa.Integer()),
        sa.Column("reopened_at", sa.DateTime(timezone=True)),
        sa.Column("reopened_by", sa.Integer()),
        sa.Column("reopen_reason", sa.String(300)),
        sa.UniqueConstraint("tenant_id", "month", name="uq_acc_period_month"),
    )
    op.create_index("ix_accounting_period_month", "accounting_period", ["tenant_id", "month"])

    # ------------------------------------------------------------------ #
    # accounting_export                                                    #
    # ------------------------------------------------------------------ #
    op.create_table(
        "accounting_export",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("period_id", sa.Integer(),
                  sa.ForeignKey("accounting_period.id", name="fk_aexport_period"), nullable=False),
        sa.Column("exported_by", sa.Integer(), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("line_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_reexport", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reexport_reason", sa.String(300)),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_accounting_export_month", "accounting_export", ["tenant_id", "month"])

    # ------------------------------------------------------------------ #
    # journal_entry                                                        #
    # ------------------------------------------------------------------ #
    op.create_table(
        "journal_entry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("number", sa.String(40), nullable=False),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CRC"),
        sa.Column("kind", sa.String(10), nullable=False, server_default="normal"),
        sa.Column("adjusts_month", sa.String(7)),
        sa.Column("period_id", sa.Integer(),
                  sa.ForeignKey("accounting_period.id", name="fk_jentry_period")),
        sa.Column("export_id", sa.Integer(),
                  sa.ForeignKey("accounting_export.id", name="fk_jentry_export")),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "source_type", "source_id", "kind", name="uq_jentry_source"),
    )
    op.create_index("ix_journal_entry_number", "journal_entry", ["number"])
    op.create_index("ix_journal_entry_date", "journal_entry", ["date"])
    op.create_index("ix_jentry_month", "journal_entry", ["tenant_id", "month"])

    # ------------------------------------------------------------------ #
    # journal_line                                                         #
    # ------------------------------------------------------------------ #
    op.create_table(
        "journal_line",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_id", sa.Integer(),
                  sa.ForeignKey("journal_entry.id", ondelete="CASCADE", name="fk_jline_entry"),
                  nullable=False),
        sa.Column("role_key", sa.String(80), nullable=False),
        sa.Column("side", sa.String(5), nullable=False),
        sa.Column("amount", sa.Numeric(16, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CRC"),
        sa.Column("fx", sa.Numeric(12, 5)),
        sa.Column("description", sa.String(200)),
        sa.Column("name", sa.String(160)),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_journal_line_entry", "journal_line", ["entry_id"])


def downgrade() -> None:
    op.drop_table("journal_line")
    op.drop_table("journal_entry")
    op.drop_table("accounting_export")
    op.drop_table("accounting_period")
    op.drop_table("accounting_role_mapping")
    op.drop_table("qbo_account")
