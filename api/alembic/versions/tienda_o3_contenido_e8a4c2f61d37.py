"""tienda (O3-01): columnas de contenido de la tienda y nombre visible en la linea del pedido.

Solo agrega columnas opcionales. product: web_name (nombre comercial), web_subtitle, web_type (tipo corregido a mano),
web_specs (especificaciones aprobadas), web_description (descripcion propia aprobada), web_manual (editado a mano: el
generador de borradores no lo pisa), warranty_months, seo_title, seo_description y gtin. order_line.display_name: el
nombre que vio el cliente; order_line.name sigue siendo el fiscal (factura y Hacienda). Nada se rellena: vacio = lo de
siempre, asi que la tienda se ve igual hasta que un lote se aprueba.

Revision ID: e8a4c2f61d37
Revises: d5f2b8c4e016
Create Date: 2026-11-11 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e8a4c2f61d37"
down_revision: str | None = "d5f2b8c4e016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRODUCT = (
    sa.Column("web_name", sa.String(120), nullable=True),
    sa.Column("web_subtitle", sa.String(160), nullable=True),
    sa.Column("web_type", sa.String(60), nullable=True),
    sa.Column("web_specs", sa.JSON(), nullable=True),
    sa.Column("web_description", sa.Text(), nullable=True),
    sa.Column("web_manual", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("warranty_months", sa.Integer(), nullable=True),
    sa.Column("seo_title", sa.String(120), nullable=True),
    sa.Column("seo_description", sa.String(200), nullable=True),
    sa.Column("gtin", sa.String(20), nullable=True),
)


def upgrade() -> None:
    for col in _PRODUCT:
        op.add_column("product", col)
    op.add_column("order_line", sa.Column("display_name", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("order_line", "display_name")
    for col in reversed(_PRODUCT):
        op.drop_column("product", col.name)
