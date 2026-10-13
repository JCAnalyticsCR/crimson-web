"""tienda_ck: checkout de la tienda en linea (metodo de pago, entrega, idempotencia, estados, comprobantes) y media privada

Pedido web: clave de idempotencia unica por empresa (doble clic = mismo pedido), datos de entrega por provincia/canton,
historial de estados, comprobantes subidos por el cliente (Media privada: nunca por la URL publica), plazo de pago,
estado de la reserva de inventario, guia de envio, motivo de cancelacion y registro de reembolso.

Revision ID: e7a3c1d95b42
Revises: b7c41e9a2d58
Create Date: 2026-10-12 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7a3c1d95b42"
down_revision: str | None = "b7c41e9a2d58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLS = [
    ("payment_kind", sa.String(16)),
    ("delivery", sa.JSON()),
    ("idempotency_key", sa.String(80)),
    ("paid_at", sa.DateTime(timezone=True)),
    ("payment_ref", sa.String(120)),
    ("tracking_number", sa.String(80)),
    ("carrier", sa.String(80)),
    ("cancel_reason", sa.Text()),
    ("refund", sa.JSON()),
    ("history", sa.JSON()),
    ("proofs", sa.JSON()),
    ("expires_at", sa.DateTime(timezone=True)),
    ("inventory_state", sa.String(12)),
    ("terms_accepted_at", sa.DateTime(timezone=True)),
]


def upgrade() -> None:
    with op.batch_alter_table("order") as b:
        for name, typ in COLS:
            b.add_column(sa.Column(name, typ, nullable=True))
        b.create_unique_constraint("uq_order_idempotency", ["tenant_id", "idempotency_key"])


def downgrade() -> None:
    with op.batch_alter_table("order") as b:
        b.drop_constraint("uq_order_idempotency", type_="unique")
        for name, _ in reversed(COLS):
            b.drop_column(name)
