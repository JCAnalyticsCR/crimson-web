"""cobranza: facturas enviadas, pausa de recordatorios y bitacora de la secuencia de cobro (plan 1.3)

Una factura "por cobrar" es la que le llego al cliente: `invoice.sent_at` (nulo = borrador sin enviar). Relleno: las
que ya estan enviada, parcial o vencida, o tienen enlace de pago o pagos, toman sent_at = created_at (el cliente ya las
tiene; las demas siguen siendo borradores y salen de "por cobrar", de "vencidas" y de los recordatorios).
`invoice.reminders_paused` y `customer.no_reminders` pausan la secuencia (por factura y por cliente; nada cambia para
las filas existentes). Tabla nueva `collection_reminder`: una fila por factura y etapa (D-3, D0, D+3, D+10).

Revision ID: c0b7a3e5d912
Revises: d7e2a9c41f58
Create Date: 2026-10-20 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c0b7a3e5d912"
down_revision: str | None = "d7e2a9c41f58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("invoice") as b:
        b.add_column(sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("reminders_paused", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("customer") as b:
        b.add_column(sa.Column("no_reminders", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.create_table(
        "collection_reminder",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoice.id", ondelete="CASCADE", name="fk_collection_reminder_invoice"), nullable=False, index=True),
        sa.Column("stage", sa.SmallInteger(), nullable=False),
        sa.Column("sent_on", sa.Date(), nullable=False, index=True),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("balance", sa.Numeric(16, 5), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("outbox_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("invoice_id", "stage", name="uq_collection_reminder_stage"),
    )

    backfill(op.get_bind())


def backfill(conn) -> None:
    """Lo que el cliente ya tiene (enviada, parcial o vencida, con enlace de pago o con pagos) queda enviado desde que se
    creo. Idempotente: solo toca las filas sin sent_at."""
    conn.execute(
        sa.text(
            "UPDATE invoice SET sent_at = created_at WHERE sent_at IS NULL AND ("
            "status IN ('enviada', 'parcial', 'vencida') "
            "OR id IN (SELECT invoice_id FROM payment_link) "
            "OR id IN (SELECT invoice_id FROM payment))"
        )
    )


def downgrade() -> None:
    op.drop_table("collection_reminder")
    with op.batch_alter_table("customer") as b:
        b.drop_column("no_reminders")
    with op.batch_alter_table("invoice") as b:
        b.drop_column("reminders_paused")
        b.drop_column("sent_at")
