"""cobranza: facturas enviadas, pausa de recordatorios y bitacora de la secuencia de cobro (plan 1.3)

Una factura "por cobrar" es la que le llego al cliente: `invoice.sent_at` (nulo = borrador sin enviar). Relleno: las
que ya estan enviada o parcial, o tienen enlace de pago o pagos, toman sent_at = created_at (el cliente ya las tiene;
las demas siguen siendo borradores y salen de "por cobrar", de "vencidas" y de los recordatorios).
"vencida" sola no prueba nada: el worker viejo (sales.mark_overdue, estados creado y parcial) y el recompute_balance
viejo pasaban a "vencida" borradores que nunca salieron, como las mensualidades que dejaba la recurrencia. Una vencida
toma sent_at solo con evidencia de envio (enlace de pago, pagos o un envio por correo que el proveedor acepto, en la
bitacora: action "send" con mail "enviado"); sin evidencia vuelve a "creado". Las importadas del historico y las que
tienen el comprobante aceptado por Hacienda ya cuentan como enviadas por su einvoice_status (services/invoice_states):
no se tocan ni toman sent_at por su estado (sin sent_at no reciben recordatorios automaticos).
downgrade quita las columnas y la tabla; no devuelve a "vencida" los borradores (el worker viejo los vuelve a marcar
en su corrida de las 00:30).
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


LINK = "EXISTS (SELECT 1 FROM payment_link pl WHERE pl.invoice_id = invoice.id)"
PAID = "EXISTS (SELECT 1 FROM payment pa WHERE pa.invoice_id = invoice.id)"
ELSEWHERE = "COALESCE(einvoice_status, '') IN ('importada', 'aceptada')"


def _sent_by_mail(conn) -> set[int]:
    """Facturas con un envio por correo aceptado por el proveedor (bitacora de doc_send: {"to", "mail"}). El JSON se
    lee en Python: igual en Postgres (dict) y en SQLite (texto)."""
    import json

    out: set[int] = set()
    rows = conn.execute(sa.text("SELECT entity_id, diff FROM audit_log WHERE action = 'send' AND entity = 'invoice' AND entity_id IS NOT NULL"))
    for entity_id, diff in rows:
        if isinstance(diff, str):
            try:
                diff = json.loads(diff)
            except ValueError:
                diff = None
        if isinstance(diff, dict) and diff.get("mail") == "enviado":
            out.add(int(entity_id))
    return out


def backfill(conn) -> None:
    """Lo que el cliente ya tiene (enviada o parcial, con enlace de pago o con pagos) queda enviado desde que se creo.
    Una "vencida" solo con evidencia de envio; sin ella vuelve a "creado" (nunca salio). Idempotente: solo toca las
    filas sin sent_at."""
    conn.execute(
        sa.text(
            "UPDATE invoice SET sent_at = created_at WHERE sent_at IS NULL AND ("
            f"(status IN ('enviada', 'parcial') AND COALESCE(einvoice_status, '') <> 'importada') OR {LINK} OR {PAID})"
        )
    )
    vencidas = conn.execute(sa.text(f"SELECT id FROM invoice WHERE status = 'vencida' AND sent_at IS NULL AND NOT ({ELSEWHERE})")).scalars().all()
    if not vencidas:
        return
    por_correo = _sent_by_mail(conn)
    for iid in vencidas:
        if iid in por_correo:
            conn.execute(sa.text("UPDATE invoice SET sent_at = created_at WHERE id = :i AND sent_at IS NULL"), {"i": iid})
        else:
            conn.execute(sa.text("UPDATE invoice SET status = 'creado' WHERE id = :i AND status = 'vencida' AND sent_at IS NULL"), {"i": iid})


def downgrade() -> None:
    op.drop_table("collection_reminder")
    with op.batch_alter_table("customer") as b:
        b.drop_column("no_reminders")
    with op.batch_alter_table("invoice") as b:
        b.drop_column("reminders_paused")
        b.drop_column("sent_at")
