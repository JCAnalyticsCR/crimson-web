"""onvo: secreto del webhook separado de la llave de API y sesion de Checkout del enlace de pago

ONVO autentica el webhook con un secreto propio (header X-Webhook-Secret, prefijo webhook_secret_) que no es la llave
secreta de la API; hasta ahora ambos compartian `secret_encrypted`. Y el enlace de pago de una factura recuerda la
ultima sesion de Checkout que abrio, para poder confirmar el pago consultando a ONVO aunque el webhook no llegue.
Columnas nuevas y nulas: nada existente cambia.

Revision ID: c5d18e3f7a2b
Revises: f6c2a8d14e9b
Create Date: 2026-10-13 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5d18e3f7a2b"
down_revision: str | None = "f6c2a8d14e9b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("payment_gateway_config") as b:
        b.add_column(sa.Column("webhook_secret_encrypted", sa.Text(), nullable=True))
    with op.batch_alter_table("payment_link") as b:
        b.add_column(sa.Column("provider_ref", sa.String(120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("payment_link") as b:
        b.drop_column("provider_ref")
    with op.batch_alter_table("payment_gateway_config") as b:
        b.drop_column("webhook_secret_encrypted")
