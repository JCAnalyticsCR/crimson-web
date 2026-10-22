"""pedidos: el pedido web tiene vendedor (reunion 21/10, items 24/42)

`order.seller_id`: el vendedor al que se le asigna el pedido web (lo asigna el admin o el vendedor lo "toma" desde la
lista). La factura que nace del pedido queda con ese vendedor y comisiona como una venta propia (Mi Tienda lo puede
apagar). `order.assigned_at`: cuando se asigno. Sin relleno: los pedidos de antes quedan sin vendedor.

`order.customer_link` (item 43): como se ligo el pedido a la ficha del cliente ("email" | "cedula") y
`order.customer_link_email`, el correo que tenia la ficha al ligarla. Los avisos van al correo actual de la ficha
salvo que el pedido se haya ligado solo por cedula a una ficha con OTRO correo: ese sigue al correo del pedido hasta
que el equipo cambie el de la ficha. Relleno de los pedidos de antes con el mismo criterio, contra el correo que la
ficha tiene hoy: si no coincide con el de la compra queda "cedula" (la cedula es publica: no se desvian sus avisos).

downgrade quita las columnas (las facturas ya creadas conservan su seller_id).

Revision ID: f3a8d2c6b917
Revises: e4c9b1f7a2d6
Create Date: 2026-10-21 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3a8d2c6b917"
down_revision: str | None = "e4c9b1f7a2d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("order") as b:
        b.add_column(sa.Column("seller_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("customer_link", sa.String(8), nullable=True))
        b.add_column(sa.Column("customer_link_email", sa.String(254), nullable=True))
        b.create_foreign_key("fk_order_seller_id", "user", ["seller_id"], ["id"])
        b.create_index("ix_order_seller_id", ["seller_id"])
    _backfill_link()


def _backfill_link() -> None:
    """customer_link de los pedidos ligados a una ficha: "email" si la ficha tiene el correo de la compra (o ninguno),
    "cedula" si tiene otro. En Python: el JSON del contacto se lee igual en SQLite y en Postgres."""
    order = sa.table(
        "order",
        sa.column("id", sa.Integer),
        sa.column("customer_id", sa.Integer),
        sa.column("contact", sa.JSON),
        sa.column("customer_link", sa.String),
        sa.column("customer_link_email", sa.String),
    )
    customer = sa.table("customer", sa.column("id", sa.Integer), sa.column("email", sa.String))
    conn = op.get_bind()
    rows = conn.execute(sa.select(order.c.id, order.c.contact, customer.c.email).select_from(order.join(customer, customer.c.id == order.c.customer_id))).all()
    for oid, contact, ficha in rows:
        compra = (((contact or {}) if isinstance(contact, dict) else {}).get("email") or "").strip().lower()
        ficha = (ficha or "").strip().lower()
        link = "email" if not ficha or ficha == compra else "cedula"
        conn.execute(sa.update(order).where(order.c.id == oid).values(customer_link=link, customer_link_email=ficha or None))


def downgrade() -> None:
    with op.batch_alter_table("order") as b:
        b.drop_index("ix_order_seller_id")
        b.drop_constraint("fk_order_seller_id", type_="foreignkey")
        b.drop_column("customer_link_email")
        b.drop_column("customer_link")
        b.drop_column("assigned_at")
        b.drop_column("seller_id")
