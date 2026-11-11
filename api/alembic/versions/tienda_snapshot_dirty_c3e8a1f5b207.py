"""tienda (O1-13): snapshot al dia. Marca de "algo cambio" en la fila del snapshot, fuera de tenant.settings.

store_snapshot.dirty_at: cuando cambio algo que la tienda muestra y que no pasa por los guardados del panel (movimientos
de inventario, reservas y liberaciones de pedidos web, retencion por margen, solicitudes de compra). La escribe
services/store_snapshot.mark_dirty_after_commit DESPUES del commit, en su propia transaccion: el checkout nunca toca la
fila de la empresa. store_snapshot.stock_v: contador que sube con cada marca (va en el ETag de /showcase y /catalog).
Aditiva: columnas nulas o con 0; el codigo viejo no las lee.

Revision ID: c3e8a1f5b207
Revises: a4c9e2b7d1f3
Create Date: 2026-11-10 22:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3e8a1f5b207"
down_revision: str | None = "a4c9e2b7d1f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("store_snapshot", sa.Column("dirty_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("store_snapshot", sa.Column("stock_v", sa.BigInteger(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("store_snapshot", "stock_v")
    op.drop_column("store_snapshot", "dirty_at")
