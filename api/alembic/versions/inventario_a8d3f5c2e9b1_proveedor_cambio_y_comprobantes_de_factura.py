"""inventario y pagos (reunion 21/10): ultimo cambio real del dato del proveedor y comprobantes de pago de facturas

`supplier_item.changed_at`: cuando cambio DE VERDAD la existencia o el costo de un item del proveedor (o cuando entro).
El panel decia "actualizado hace 3 h" (la hora de la consulta) aunque la lista no cambiaba hace dias; ahora dice las dos
cosas. Sin relleno: lo que ya existe cuenta desde la fecha de su lista hasta el primer cambio que se vea.

`invoice_payment_proof`: el comprobante de SINPE o transferencia que el cliente sube desde la pagina publica de pago de
la factura (/pagar). Entra a la misma revision que los comprobantes de pedidos web y registra el pago al aprobarse.

Revision ID: a8d3f5c2e9b1
Revises: e4c9b1f7a2d6
Create Date: 2026-10-21 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a8d3f5c2e9b1"
down_revision: str | None = "e4c9b1f7a2d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("supplier_item") as b:
        b.add_column(sa.Column("changed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "invoice_payment_proof",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoice.id", ondelete="CASCADE", name="fk_invoice_payment_proof_invoice"), nullable=False),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("media.id", name="fk_invoice_payment_proof_media"), nullable=False),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(16, 5), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CRC"),
        sa.Column("reference", sa.String(120), nullable=True),
        sa.Column("payer_name", sa.String(160), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="pendiente"),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payment.id", ondelete="SET NULL", name="fk_invoice_payment_proof_payment"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_invoice_payment_proof_tenant_id", "invoice_payment_proof", ["tenant_id"])
    op.create_index("ix_invoice_payment_proof_invoice_id", "invoice_payment_proof", ["invoice_id"])
    op.create_index("ix_invoice_payment_proof_status", "invoice_payment_proof", ["status"])


def downgrade() -> None:
    op.drop_index("ix_invoice_payment_proof_status", table_name="invoice_payment_proof")
    op.drop_index("ix_invoice_payment_proof_invoice_id", table_name="invoice_payment_proof")
    op.drop_index("ix_invoice_payment_proof_tenant_id", table_name="invoice_payment_proof")
    op.drop_table("invoice_payment_proof")
    with op.batch_alter_table("supplier_item") as b:
        b.drop_column("changed_at")
