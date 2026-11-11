"""tienda (EX-OPT): consentimiento para ofertas en el pedido web.

order.marketing_opt_in: la persona marco en el checkout «Quiero recibir ofertas y novedades ... por correo o
WhatsApp» (casilla opcional y desmarcada). order.marketing_opt_in_at: cuando lo acepto. La Politica de privacidad dice
que la publicidad solo va a quien la acepte aparte: esta es esa aceptacion, con su fecha. Aditiva: los pedidos viejos
quedan en «no» (nadie acepto nada).

Revision ID: d5f2b8c4e016
Revises: c3e8a1f5b207
Create Date: 2026-11-10 23:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d5f2b8c4e016"
down_revision: str | None = "c3e8a1f5b207"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("order", sa.Column("marketing_opt_in", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("order", sa.Column("marketing_opt_in_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("order", "marketing_opt_in_at")
    op.drop_column("order", "marketing_opt_in")
