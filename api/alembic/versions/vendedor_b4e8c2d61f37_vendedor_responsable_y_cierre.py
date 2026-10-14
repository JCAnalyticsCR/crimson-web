"""vendedor: vendedor responsable en cotizaciones y facturas, fecha de cierre de oportunidades y notas sin costo

El dueno de una cotizacion o factura era quien la tecleaba (created_by): la cotizacion que la oficina genera desde el
levantamiento quedaba del admin y la vendedora no veia la de su propia oportunidad (ni cobraba su comision). Ahora
cada documento lleva su vendedor responsable (seller_id); created_by queda como "quien lo tecleo" (auditoria).
Ver app/services/ownership.py.

Relleno:
- cotizacion ligada a una oportunidad con responsable -> ese responsable; el resto -> quien la tecleo.
- factura de una cotizacion -> el vendedor de la cotizacion; el resto -> quien la tecleo, salvo las facturas de pedidos
  de la tienda en linea, que quedan sin vendedor (no comisionan).
- oportunidades ganadas o perdidas: closed_at = su ultima modificacion (no hay mejor dato).
- notas internas de cotizaciones y facturas: se quita "Costo estimado ₡… · margen … %." que escribia el paso
  levantamiento -> cotizacion (mismo patron que app/routers/fieldwork.COST_NOTE_RE). Idempotente.

downgrade quita las columnas. La limpieza de notas NO se revierte (no se vuelve a escribir el costo en un texto que
leen roles que no ven costos). Las comisiones ya devengadas no se recalculan.

Revision ID: b4e8c2d61f37
Revises: a4f7c2e9d613
Create Date: 2026-10-14 12:00:00
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b4e8c2d61f37"
down_revision: str | None = "a4f7c2e9d613"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# copia de app/routers/fieldwork.COST_NOTE_RE (una prueba verifica que sean iguales)
COST_NOTE_RE = re.compile(r" ?Costo estimado ₡[\d.,]+ · margen (?:-?[\d.,]+|None) %\.")

BACKFILL = (
    """UPDATE quote SET seller_id = (SELECT o.owner_id FROM opportunity o
        WHERE o.quote_id = quote.id AND o.owner_id IS NOT NULL ORDER BY o.id LIMIT 1)
     WHERE seller_id IS NULL""",
    "UPDATE quote SET seller_id = created_by WHERE seller_id IS NULL",
    """UPDATE invoice SET seller_id = (SELECT q.seller_id FROM quote q WHERE q.id = invoice.quote_id)
     WHERE seller_id IS NULL AND quote_id IS NOT NULL""",
    """UPDATE invoice SET seller_id = created_by
     WHERE seller_id IS NULL
       AND id NOT IN (SELECT invoice_id FROM "order" WHERE channel = 'tienda' AND invoice_id IS NOT NULL)""",
    "UPDATE opportunity SET closed_at = updated_at WHERE status IN ('ganada', 'perdida') AND closed_at IS NULL",
)


def clean_notes(bind) -> int:
    """Quita el costo y el margen de las notas internas. Solo escribe las filas que cambian."""
    n = 0
    for table in ("quote", "invoice"):
        rows = bind.execute(sa.text(f"SELECT id, internal_notes FROM {table} WHERE internal_notes LIKE '%Costo estimado%'")).all()
        for rid, txt in rows:
            limpio = COST_NOTE_RE.sub("", txt or "").strip()
            if limpio != (txt or ""):
                bind.execute(sa.text(f"UPDATE {table} SET internal_notes = :t WHERE id = :i"), {"t": limpio or None, "i": rid})
                n += 1
    return n


def backfill(bind) -> None:
    """Relleno de datos (separado para probarlo sin recrear tablas). Solo toca filas sin dato: se puede repetir."""
    for sql in BACKFILL:
        bind.execute(sa.text(sql))
    clean_notes(bind)


def upgrade() -> None:
    with op.batch_alter_table("quote") as b:
        b.add_column(sa.Column("seller_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_quote_seller_id", "user", ["seller_id"], ["id"])
        b.create_index("ix_quote_seller_id", ["seller_id"])
    with op.batch_alter_table("invoice") as b:
        b.add_column(sa.Column("seller_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_invoice_seller_id", "user", ["seller_id"], ["id"])
        b.create_index("ix_invoice_seller_id", ["seller_id"])
    with op.batch_alter_table("opportunity") as b:
        b.add_column(sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    backfill(op.get_bind())


def downgrade() -> None:
    with op.batch_alter_table("opportunity") as b:
        b.drop_column("closed_at")
    with op.batch_alter_table("invoice") as b:
        b.drop_index("ix_invoice_seller_id")
        b.drop_constraint("fk_invoice_seller_id", type_="foreignkey")
        b.drop_column("seller_id")
    with op.batch_alter_table("quote") as b:
        b.drop_index("ix_quote_seller_id")
        b.drop_constraint("fk_quote_seller_id", type_="foreignkey")
        b.drop_column("seller_id")
