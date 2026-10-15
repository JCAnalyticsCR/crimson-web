"""comisiones v2: reglas por beneficiario y evento, libro unico de ganancias (vendedor y aliado) y rentabilidad

Revision ID: a3f7c9e2d415
Revises: e9c4a7b21d63
Create Date: 2026-10-14

Agrega:
- commission_rule: beneficiario (vendedor | aliado), empresa aliada o participacion (acuerdo de un proyecto), tipo
  (comision | bono_fin | bono_anticipo), cuando se devenga (cobro | cotizacion_aceptada | proyecto_completado),
  filtros (tipo de negocio, solucion, vigencia) y monto fijo como alternativa al %.
- commission: user_id pasa a nullable (los movimientos de aliado no tienen usuario); beneficiario, tipo, evento,
  documentos ligados (cotizacion, proyecto, oportunidad, precio del aliado), source_key unico por empresa
  (idempotencia de cada devengo), amount_crc/fx para totales en colones, terms (foto de la regla), concepto,
  motivo de espera, quien aprobo/pago y cuando, comprobante, factura del aliado ligada, motivo de anulacion y el
  movimiento que revierte.
- project.completed_at: la primera vez que el proyecto queda terminado/entregado/facturado.

La empresa aliada (ally_company y sus FK) ya la creo e9c4a7b21d63: aqui no se toca.

Nada existente cambia de significado: las reglas viejas quedan como "vendedor · comision · al cobrar" (lo que ya
hacian) y los movimientos viejos como "vendedor · comision · cobro". Relleno: source_key de los movimientos con
pago (misma clave que usa el motor, asi un cobro viejo no comisiona dos veces), amount_crc de los que estan en
colones (los de otra moneda quedan null: los lectores usan amount x tipo de cambio de hoy) y completed_at de los
proyectos ya cerrados. NO se devengan comisiones historicas.

downgrade: borra los movimientos de aliados (perdida de datos: no tienen usuario y user_id vuelve a NOT NULL), borra
las reglas de aliado, por participacion y los bonos, desactiva las de vendedor que el esquema viejo no sabe leer y
quita columnas y restriccion en orden inverso.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3f7c9e2d415"
down_revision: str | None = "e9c4a7b21d63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("commission_rule") as b:
        b.add_column(sa.Column("beneficiary", sa.String(length=10), server_default="vendedor", nullable=False))
        b.add_column(sa.Column("ally_company_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("participation_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("kind", sa.String(length=14), server_default="comision", nullable=False))
        b.add_column(sa.Column("trigger", sa.String(length=20), server_default="cobro", nullable=False))
        b.add_column(sa.Column("deal_kind", sa.String(length=12), nullable=True))
        b.add_column(sa.Column("solution", sa.String(length=20), nullable=True))
        b.add_column(sa.Column("fixed_amount", sa.Numeric(16, 2), nullable=True))
        b.add_column(sa.Column("fixed_currency", sa.String(length=3), nullable=True))
        b.add_column(sa.Column("committed_date", sa.Date(), nullable=True))
        b.add_column(sa.Column("early_days", sa.Integer(), nullable=True))
        b.add_column(sa.Column("valid_from", sa.Date(), nullable=True))
        b.add_column(sa.Column("valid_to", sa.Date(), nullable=True))
        b.create_foreign_key("fk_crule_ally_company", "ally_company", ["ally_company_id"], ["id"])
        b.create_foreign_key("fk_crule_participation", "ally_participation", ["participation_id"], ["id"], ondelete="CASCADE")
        b.create_index("ix_commission_rule_ally_company_id", ["ally_company_id"])
        b.create_index("ix_commission_rule_participation_id", ["participation_id"])

    with op.batch_alter_table("commission") as b:
        b.alter_column("user_id", existing_type=sa.Integer(), nullable=True)
        b.add_column(sa.Column("beneficiary", sa.String(length=10), server_default="vendedor", nullable=False))
        b.add_column(sa.Column("ally_company_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("participation_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("kind", sa.String(length=14), server_default="comision", nullable=False))
        b.add_column(sa.Column("trigger", sa.String(length=20), server_default="cobro", nullable=False))
        b.add_column(sa.Column("quote_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("project_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("opportunity_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("cost_request_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("source_key", sa.String(length=120), nullable=True))
        b.add_column(sa.Column("fx", sa.Numeric(12, 5), nullable=True))
        b.add_column(sa.Column("amount_crc", sa.Numeric(16, 2), nullable=True))
        b.add_column(sa.Column("terms", sa.JSON(), nullable=True))
        b.add_column(sa.Column("description", sa.String(length=240), nullable=True))
        b.add_column(sa.Column("hold_reason", sa.String(length=240), nullable=True))
        b.add_column(sa.Column("approved_by", sa.Integer(), nullable=True))
        b.add_column(sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("paid_by", sa.Integer(), nullable=True))
        b.add_column(sa.Column("paid_ref", sa.String(length=120), nullable=True))
        b.add_column(sa.Column("expense_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("void_reason", sa.String(length=240), nullable=True))
        b.add_column(sa.Column("reverses_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_comm_ally_company", "ally_company", ["ally_company_id"], ["id"])
        b.create_foreign_key("fk_comm_participation", "ally_participation", ["participation_id"], ["id"], ondelete="SET NULL")
        b.create_foreign_key("fk_comm_quote", "quote", ["quote_id"], ["id"], ondelete="SET NULL")
        b.create_foreign_key("fk_comm_project", "project", ["project_id"], ["id"], ondelete="SET NULL")
        b.create_foreign_key("fk_comm_opportunity", "opportunity", ["opportunity_id"], ["id"], ondelete="SET NULL")
        b.create_foreign_key("fk_comm_cost_request", "ally_cost_request", ["cost_request_id"], ["id"], ondelete="SET NULL")
        b.create_foreign_key("fk_comm_approved_by", "user", ["approved_by"], ["id"])
        b.create_foreign_key("fk_comm_paid_by", "user", ["paid_by"], ["id"])
        b.create_foreign_key("fk_comm_expense", "expense", ["expense_id"], ["id"], ondelete="SET NULL")
        b.create_foreign_key("fk_comm_reverses", "commission", ["reverses_id"], ["id"])
        b.create_unique_constraint("uq_commission_source", ["tenant_id", "source_key"])
        b.create_index("ix_commission_ally_company_id", ["ally_company_id"])
        b.create_index("ix_commission_participation_id", ["participation_id"])
        b.create_index("ix_commission_quote_id", ["quote_id"])
        b.create_index("ix_commission_project_id", ["project_id"])

    with op.batch_alter_table("project") as b:
        b.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))

    # Relleno portable (SQLite y Postgres). Un pago viejo ya comisiono: su clave queda tomada.
    op.execute(
        "UPDATE commission SET source_key = 'cobro:' || CAST(payment_id AS VARCHAR(20)) || ':v:' || CAST(user_id AS VARCHAR(20)) "
        "WHERE payment_id IS NOT NULL AND user_id IS NOT NULL"
    )
    op.execute("UPDATE commission SET amount_crc = amount, fx = 1 WHERE currency = 'CRC'")
    op.execute("UPDATE project SET completed_at = COALESCE(delivered_at, updated_at) WHERE status IN ('terminado', 'entregado', 'facturado')")


def downgrade() -> None:
    # perdida de datos: los movimientos de aliados no tienen usuario y user_id vuelve a NOT NULL
    op.execute("DELETE FROM commission WHERE beneficiary = 'aliado' OR user_id IS NULL")
    # Reglas que el esquema viejo no sabe leer. Sin beneficiary/kind/participation_id, el codigo anterior
    # ("user_id IS NULL AND active" = regla general de vendedores) tomaria una regla de aliados, un acuerdo de
    # proyecto o un bono como comision de todos los vendedores. Las de aliado, por participacion y los bonos se
    # borran (primero sueltan los movimientos de vendedor que las usaron: commission.rule_id es NO ACTION). Las de
    # vendedor con algo que el esquema viejo no tiene (otro evento, filtros, vigencia, monto fijo) se desactivan
    # para que no comisionen distinto de lo pactado; se ven en la lista y el admin decide.
    nuevas = "beneficiary = 'aliado' OR participation_id IS NOT NULL OR kind <> 'comision'"
    op.execute(f"UPDATE commission SET rule_id = NULL WHERE rule_id IN (SELECT id FROM commission_rule WHERE {nuevas})")
    op.execute(f"DELETE FROM commission_rule WHERE {nuevas}")
    op.execute(
        "UPDATE commission_rule SET active = false WHERE \"trigger\" <> 'cobro' OR deal_kind IS NOT NULL OR solution IS NOT NULL "
        "OR valid_from IS NOT NULL OR valid_to IS NOT NULL OR fixed_amount IS NOT NULL"
    )

    with op.batch_alter_table("project") as b:
        b.drop_column("completed_at")

    with op.batch_alter_table("commission") as b:
        b.drop_index("ix_commission_project_id")
        b.drop_index("ix_commission_quote_id")
        b.drop_index("ix_commission_participation_id")
        b.drop_index("ix_commission_ally_company_id")
        b.drop_constraint("uq_commission_source", type_="unique")
        for fk in (
            "fk_comm_reverses",
            "fk_comm_expense",
            "fk_comm_paid_by",
            "fk_comm_approved_by",
            "fk_comm_cost_request",
            "fk_comm_opportunity",
            "fk_comm_project",
            "fk_comm_quote",
            "fk_comm_participation",
            "fk_comm_ally_company",
        ):
            b.drop_constraint(fk, type_="foreignkey")
        for col in (
            "reverses_id",
            "void_reason",
            "expense_id",
            "paid_ref",
            "paid_by",
            "approved_at",
            "approved_by",
            "hold_reason",
            "description",
            "terms",
            "amount_crc",
            "fx",
            "source_key",
            "cost_request_id",
            "opportunity_id",
            "project_id",
            "quote_id",
            "trigger",
            "kind",
            "participation_id",
            "ally_company_id",
            "beneficiary",
        ):
            b.drop_column(col)
        b.alter_column("user_id", existing_type=sa.Integer(), nullable=False)

    with op.batch_alter_table("commission_rule") as b:
        b.drop_index("ix_commission_rule_participation_id")
        b.drop_index("ix_commission_rule_ally_company_id")
        b.drop_constraint("fk_crule_participation", type_="foreignkey")
        b.drop_constraint("fk_crule_ally_company", type_="foreignkey")
        for col in (
            "valid_to",
            "valid_from",
            "early_days",
            "committed_date",
            "fixed_currency",
            "fixed_amount",
            "solution",
            "deal_kind",
            "trigger",
            "kind",
            "participation_id",
            "ally_company_id",
            "beneficiary",
        ):
            b.drop_column(col)
