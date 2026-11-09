"""pagos (09/11): Tienda de pruebas aislada con ONVO modo Test

- onvo_test_config (nueva): llaves de PRUEBAS de ONVO por empresa (una fila por empresa), cifradas con FISCAL_KEYS y AAD
  propia. payment_gateway_config (la fila live) NO se toca: ni columnas nuevas, ni migracion de datos, ni recifrado.
- store_test_order (nueva): pedidos de la Tienda de pruebas, sin FK a pedidos, clientes, productos, facturas ni
  secuencias. CHECK moneda CRC, total entre 0 y 2 000, estados conocidos; numero unico por empresa (PRB-0001).
- webhook_event.tenant_id (nueva, sin FK) + indice (tenant_id, provider, id): el estado del webhook se filtra por
  empresa (antes una empresa veia el ultimo aviso de otra).
- hacienda_test_run.origin (nueva, 'ajustes' por defecto): de donde salio la prueba real con Hacienda.

downgrade: borra las dos tablas, el indice y las dos columnas. Los pedidos y llaves de pruebas se pierden; nada real cambia.

Revision ID: a1c4e7b9d203
Revises: f3a9c2d7b514
Create Date: 2026-11-09 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1c4e7b9d203"
down_revision: str | None = "f3a9c2d7b514"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATES = "status IN ('nuevo', 'en_onvo', 'en_proceso', 'pagado', 'fallido', 'cancelado', 'vencido', 'revisar')"


def upgrade() -> None:
    op.create_table(
        "onvo_test_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("publishable_key", sa.String(length=200), nullable=True),
        sa.Column("secret_enc", sa.Text(), nullable=True),
        sa.Column("webhook_secret_enc", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_onvo_test_config_tenant"),
    )
    with op.batch_alter_table("onvo_test_config") as b:
        b.create_index("ix_onvo_test_config_tenant_id", ["tenant_id"])

    op.create_table(
        "store_test_order",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("subtotal", sa.Numeric(14, 2), nullable=False),
        sa.Column("tax", sa.Numeric(14, 2), nullable=False),
        sa.Column("total", sa.Numeric(14, 2), nullable=False),
        sa.Column("lines", sa.JSON(), nullable=False),
        sa.Column("buyer", sa.JSON(), nullable=False),
        sa.Column("onvo_sessions", sa.JSON(), nullable=False),
        sa.Column("onvo_intent_id", sa.String(length=120), nullable=True),
        sa.Column("paid_method", sa.String(length=16), nullable=True),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("timeline", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hacienda_test_run_id", sa.Integer(), nullable=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.CheckConstraint("currency = 'CRC'", name="ck_store_test_order_crc"),
        sa.CheckConstraint("total > 0 AND total <= 2000", name="ck_store_test_order_total"),
        sa.CheckConstraint(STATES, name="ck_store_test_order_status"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "number", name="uq_store_test_order_number"),
    )
    with op.batch_alter_table("store_test_order") as b:
        b.create_index("ix_store_test_order_tenant_id", ["tenant_id"])
        b.create_index("ix_store_test_order_tenant_created", ["tenant_id", "created_at"])

    with op.batch_alter_table("webhook_event") as b:
        b.add_column(sa.Column("tenant_id", sa.Integer(), nullable=True))
        b.create_index("ix_webhook_event_tenant_provider", ["tenant_id", "provider", "id"])

    with op.batch_alter_table("hacienda_test_run") as b:
        b.add_column(sa.Column("origin", sa.String(length=16), nullable=False, server_default="ajustes"))


def downgrade() -> None:
    with op.batch_alter_table("hacienda_test_run") as b:
        b.drop_column("origin")
    with op.batch_alter_table("webhook_event") as b:
        b.drop_index("ix_webhook_event_tenant_provider")
        b.drop_column("tenant_id")
    with op.batch_alter_table("store_test_order") as b:
        b.drop_index("ix_store_test_order_tenant_created")
        b.drop_index("ix_store_test_order_tenant_id")
    op.drop_table("store_test_order")
    with op.batch_alter_table("onvo_test_config") as b:
        b.drop_index("ix_onvo_test_config_tenant_id")
    op.drop_table("onvo_test_config")
