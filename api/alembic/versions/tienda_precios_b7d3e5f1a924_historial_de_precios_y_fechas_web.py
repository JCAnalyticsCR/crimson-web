"""tienda (O0-07): historial de precios y fechas de cambio publico. Solo registra: no cambia ningun precio.

- product_price_history (nueva): una fila por cambio de precio de venta, precio web, moneda o IVA de un producto
  (services/price_history). Indice por producto y fecha. FK al producto con ON DELETE CASCADE (la purga de la papelera
  sigue funcionando); changed_by sin FK, como los *_by del archivo.
- product.web_updated_at y product.hidden_since (timestamptz, nulas): ultima vez que cambio algo que el cliente ve, y
  desde cuando un producto publicado no sale en la tienda (lo fija el rearmado del snapshot).
- Datos: una fila "inicial" por producto con el precio que tiene hoy (no se puede reconstruir hacia atras); las fechas
  nuevas quedan nulas (sin dato).

downgrade: borra la tabla, su indice y las dos columnas. Ningun precio se toca ni al subir ni al bajar.

Revision ID: b7d3e5f1a924
Revises: c8e2f1a4b937
Create Date: 2026-11-10 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7d3e5f1a924"
down_revision: str | None = "c8e2f1a4b937"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RATE = "COALESCE((SELECT t.rate FROM product_tax pt JOIN tax t ON t.id = pt.tax_id WHERE pt.product_id = p.id ORDER BY pt.id LIMIT 1), 13)"


def upgrade() -> None:
    with op.batch_alter_table("product") as b:
        b.add_column(sa.Column("web_updated_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("hidden_since", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "product_price_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("price_net", sa.Numeric(14, 5), nullable=False),
        sa.Column("web_price_net", sa.Numeric(14, 5), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("tax_rate", sa.Numeric(6, 3), nullable=False),
        sa.Column("price_final", sa.Numeric(14, 2), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("changed_by", sa.Integer(), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["product.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("product_price_history") as b:
        b.create_index("ix_product_price_history_tenant_id", ["tenant_id"])
        b.create_index("ix_product_price_history_product_valid", ["product_id", "valid_from"])

    # fila inicial: el precio de hoy de cada producto (origen "inicial", sin usuario)
    op.execute(
        sa.text(
            "INSERT INTO product_price_history (tenant_id, product_id, price_net, web_price_net, currency, tax_rate, price_final, source, changed_by, valid_from)"
            f" SELECT p.tenant_id, p.id, COALESCE(p.price, 0), p.web_price, UPPER(COALESCE(p.currency, 'CRC')), {RATE},"
            f" ROUND(COALESCE(p.web_price, p.price, 0) * (1 + {RATE} / 100.0), 2), 'inicial', NULL, CURRENT_TIMESTAMP"
            " FROM product p"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("product_price_history") as b:
        b.drop_index("ix_product_price_history_product_valid")
        b.drop_index("ix_product_price_history_tenant_id")
    op.drop_table("product_price_history")
    with op.batch_alter_table("product") as b:
        b.drop_column("hidden_since")
        b.drop_column("web_updated_at")
