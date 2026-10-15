"""facturacion: recepcion de comprobantes, conexion con Hacienda, gastos ligados a XML y bancos

Tablas nuevas: fiscal_profile (datos del emisor), fiscal_activity (actividades economicas), fiscal_credential
(credenciales de Hacienda cifradas con FISCAL_KEYS), reception_mailbox (estado del buzon IMAP con arriendo),
reception_message (cada correo o carga manual), bank_instrument (chequeras y tarjetas de debito, solo ultimos 4) y
reception_response (cada intento de mensaje receptor, firmado una sola vez).

Columnas nuevas: received_document (tipo, version, receptor, tipo de cambio, IVA, estado, plazo, respuesta del
proveedor, credito y gasto aplicable, monto e IVA aceptados en una parcial, descarte), expense (comprobante ligado,
tipo de cambio, IVA acreditable, pago, recurrencia, chequera/tarjeta), expense_category.d151_kind,
recurrence.anchor_day, bank_account.opening_*, einvoice_document (modo, reintentos, entrega al cliente, mensaje
receptor del cliente). Unico (tenant_id, clave) en received_document, (tenant_id, recurrence_id, recurrence_date) en
expense y (tenant_id, mode, consecutive) en reception_response (T0x copia sucursal y terminal de F0x).

Duplicados por clave antes del unico: si todas menos una son copias exactas sin respuesta (mismo XML, sin accion ni
gasto) se borran las copias (queda la de menor id, o la que tenga accion/gasto) y queda una fila en audit_log
(action="dedupe_migracion"). Si hay dos con respuesta o gasto, o XML distinto, se aborta con los ids
(herramienta previa: python -m app.tools.check_reception_dupes).

Relleno (backfill, idempotente, solo toca filas sin dato):
- fiscal_profile por empresa (tipo 02 si la cedula tiene 10 digitos, 01 si 9), ambiente pruebas.
- fiscal_activity desde tenant.settings["activity_codes"] (la primera por defecto).
- received_document: issuer_activity_code = activity_code (la actividad vieja era la del proveedor) y se limpia
  activity_code en las no respondidas; status por_revisar/respondido; hacienda_status aprobado -> simulado,
  pendiente con respuesta -> simulado (nunca se enviaron), pendiente sin respuesta -> sin_respuesta; doc_type desde
  la raiz del XML; deadline (8 dias habiles desde el 1.o del mes siguiente). iva_total queda NULL (cae a tax_total).
- expense: received_document_id desde la recepcion que lo apunta; fx_rate desde exchange_rate (venta de esa fecha o
  la anterior, si no 1); tax_credit_amount: credito -> IVA, no_credito -> 0, proporcional -> NULL.
- recurrence.anchor_day = dia de next_date.

downgrade quita tablas, columnas, unicos e indices. Antes de achicar iva_condition / iva_credit a 12 caracteres lleva
bienes_capital -> credito y credito_parcial -> proporcional. NO se revierte aprobado -> simulado (el "aprobado" viejo
era falso: nunca se envio nada a Hacienda) ni el borrado de duplicados exactos.

Revision ID: f1c7a9d3e520
Revises: b4e8c2d61f37
Create Date: 2026-10-14 18:00:00
"""

import json
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

import sqlalchemy as sa

from alembic import op

revision: str = "f1c7a9d3e520"
down_revision: str | None = "b4e8c2d61f37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# copia de app/services/reception_calc.deadline (una prueba verifica que den lo mismo)
ROOT_RE = re.compile(
    r"<(?:\w+:)?(FacturaElectronica|TiqueteElectronico|NotaCreditoElectronica|NotaDebitoElectronica|"
    r"FacturaElectronicaCompra|FacturaElectronicaExportacion|ReciboElectronicoPago)\b"
)
ROOT_TYPE = {
    "FacturaElectronica": "FE",
    "TiqueteElectronico": "TE",
    "NotaCreditoElectronica": "NC",
    "NotaDebitoElectronica": "ND",
    "FacturaElectronicaCompra": "FEC",
    "FacturaElectronicaExportacion": "FEE",
    "ReciboElectronicoPago": "REP",
}
ACTIVITY_RE = re.compile(r"^[0-9A-Za-z.]{6}$")


def deadline(issue: date | None) -> date | None:
    """8 dias habiles (lunes a viernes) contados desde el primer dia del mes siguiente (el dia 1 cuenta si es habil)."""
    if not issue:
        return None
    d = (issue.replace(day=28) + timedelta(days=4)).replace(day=1)
    n = 0
    while True:
        if d.weekday() < 5:
            n += 1
            if n == 8:
                return d
        d += timedelta(days=1)


def _json(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def _date(v):
    if v is None or isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


audit_t = sa.table(
    "audit_log",
    sa.column("tenant_id", sa.Integer),
    sa.column("user_id", sa.Integer),
    sa.column("at", sa.DateTime(timezone=True)),
    sa.column("action", sa.String),
    sa.column("entity", sa.String),
    sa.column("entity_id", sa.Integer),
    sa.column("diff", sa.JSON),
)
profile_t = sa.table(
    "fiscal_profile",
    sa.column("tenant_id", sa.Integer),
    sa.column("id_type", sa.String),
    sa.column("phone_country", sa.String),
    sa.column("environment", sa.String),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
activity_t = sa.table(
    "fiscal_activity",
    sa.column("tenant_id", sa.Integer),
    sa.column("code", sa.String),
    sa.column("description", sa.String),
    sa.column("is_default", sa.Boolean),
    sa.column("active", sa.Boolean),
)


def dedupe_receptions(bind) -> list[int]:
    """Borra copias exactas sin respuesta de una misma clave. Devuelve los ids borrados. Aborta si no se puede decidir."""
    borrados: list[int] = []
    grupos = bind.execute(
        sa.text("SELECT tenant_id, clave, COUNT(*) FROM received_document GROUP BY tenant_id, clave HAVING COUNT(*) > 1")
    ).all()
    for tid, clave, _n in grupos:
        rows = bind.execute(
            sa.text("SELECT id, xml_document, action, expense_id FROM received_document WHERE tenant_id = :t AND clave = :c ORDER BY id"),
            {"t": tid, "c": clave},
        ).all()
        vivas = [r for r in rows if r.action is not None or r.expense_id is not None]
        xmls = {r.xml_document for r in rows}
        if len(vivas) > 1 or len(xmls) > 1:
            raise RuntimeError(f"Recepciones duplicadas por clave (ids {', '.join(str(r.id) for r in rows)}): resolver a mano antes de migrar")
        queda = vivas[0] if vivas else rows[0]
        ids = [r.id for r in rows if r.id != queda.id]
        bind.execute(sa.text("DELETE FROM received_document WHERE id IN (" + ",".join(str(i) for i in ids) + ")"))
        bind.execute(
            audit_t.insert().values(
                tenant_id=tid,
                user_id=None,
                at=datetime.now(UTC),
                action="dedupe_migracion",
                entity="received_document",
                entity_id=queda.id,
                diff={"clave": clave, "borrados": ids, "queda": queda.id},
            )
        )
        borrados += ids
    return borrados


def backfill(bind) -> None:
    """Relleno de datos (separado para probarlo sin recrear tablas). Solo toca filas sin dato: se puede repetir."""
    now = datetime.now(UTC)
    # ---- perfil fiscal y actividades por empresa
    for tid, tax_id, raw_settings in bind.execute(sa.text("SELECT id, tax_id, settings FROM tenant")).all():
        if bind.execute(sa.text("SELECT 1 FROM fiscal_profile WHERE tenant_id = :t"), {"t": tid}).first() is None:
            digits = "".join(ch for ch in (tax_id or "") if ch.isdigit())
            id_type = "01" if len(digits) == 9 else "02"
            bind.execute(profile_t.insert().values(tenant_id=tid, id_type=id_type, phone_country="506", environment="pruebas", created_at=now, updated_at=now))
        if bind.execute(sa.text("SELECT 1 FROM fiscal_activity WHERE tenant_id = :t"), {"t": tid}).first() is None:
            codes = [c for c in ((_json(raw_settings) or {}).get("activity_codes") or []) if isinstance(c, str) and ACTIVITY_RE.match(c)]
            for i, code in enumerate(dict.fromkeys(codes)):
                bind.execute(activity_t.insert().values(tenant_id=tid, code=code, description=None, is_default=i == 0, active=True))

    # ---- recepciones
    for sql in (
        "UPDATE received_document SET issuer_activity_code = activity_code WHERE issuer_activity_code IS NULL AND activity_code IS NOT NULL",
        "UPDATE received_document SET activity_code = NULL WHERE action IS NULL AND activity_code = issuer_activity_code",
        "UPDATE received_document SET status = 'respondido' WHERE action IS NOT NULL AND status = 'por_revisar' AND responded_at IS NULL",
        "UPDATE received_document SET hacienda_status = 'simulado' WHERE hacienda_status = 'aprobado'",
        "UPDATE received_document SET hacienda_status = 'simulado' WHERE hacienda_status = 'pendiente' AND action IS NOT NULL",
        "UPDATE received_document SET hacienda_status = 'sin_respuesta' WHERE hacienda_status = 'pendiente' AND action IS NULL",
    ):
        bind.execute(sa.text(sql))
    for rid, xml in bind.execute(sa.text("SELECT id, xml_document FROM received_document WHERE doc_type = 'FE' AND xml_document IS NOT NULL")).all():
        m = ROOT_RE.search((xml or "")[:600])
        if m and ROOT_TYPE[m.group(1)] != "FE":
            bind.execute(sa.text("UPDATE received_document SET doc_type = :d WHERE id = :i"), {"d": ROOT_TYPE[m.group(1)], "i": rid})
    for rid, issue in bind.execute(sa.text("SELECT id, issue_date FROM received_document WHERE deadline IS NULL AND issue_date IS NOT NULL")).all():
        bind.execute(sa.text("UPDATE received_document SET deadline = :d WHERE id = :i"), {"d": deadline(_date(issue)), "i": rid})

    # ---- gastos
    bind.execute(
        sa.text(
            """UPDATE expense SET received_document_id = (SELECT r.id FROM received_document r WHERE r.expense_id = expense.id ORDER BY r.id LIMIT 1)
               WHERE received_document_id IS NULL AND id IN (SELECT expense_id FROM received_document WHERE expense_id IS NOT NULL)"""
        )
    )
    for eid, cur, edate in bind.execute(sa.text("SELECT id, currency, date FROM expense WHERE currency <> 'CRC' AND fx_rate = 1")).all():
        fx = bind.execute(
            sa.text("SELECT sell FROM exchange_rate WHERE currency = :c AND date <= :d ORDER BY date DESC LIMIT 1"), {"c": cur, "d": _date(edate)}
        ).scalar()
        if fx:
            bind.execute(sa.text("UPDATE expense SET fx_rate = :f WHERE id = :i"), {"f": fx, "i": eid})
    bind.execute(sa.text("UPDATE expense SET tax_credit_amount = tax_amount WHERE tax_credit_amount IS NULL AND iva_credit = 'credito'"))
    bind.execute(sa.text("UPDATE expense SET tax_credit_amount = 0 WHERE tax_credit_amount IS NULL AND iva_credit = 'no_credito'"))

    # ---- recurrencias
    for rid, nd in bind.execute(sa.text("SELECT id, next_date FROM recurrence WHERE anchor_day IS NULL")).all():
        if nd is not None:
            bind.execute(sa.text("UPDATE recurrence SET anchor_day = :a WHERE id = :i"), {"a": _date(nd).day, "i": rid})


def upgrade() -> None:
    # 1. tablas nuevas (en orden por llaves foraneas)
    op.create_table(
        "fiscal_profile",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("id_type", sa.String(length=2), nullable=False, server_default="02"),
        sa.Column("commercial_name", sa.String(length=160), nullable=True),
        sa.Column("email", sa.String(length=200), nullable=True),
        sa.Column("phone_country", sa.String(length=3), nullable=False, server_default="506"),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("province", sa.String(length=1), nullable=True),
        sa.Column("canton", sa.String(length=2), nullable=True),
        sa.Column("district", sa.String(length=2), nullable=True),
        sa.Column("district_name", sa.String(length=80), nullable=True),
        sa.Column("barrio", sa.String(length=50), nullable=True),
        sa.Column("other_signs", sa.String(length=250), nullable=True),
        sa.Column("environment", sa.String(length=12), nullable=False, server_default="pruebas"),
        sa.Column("production_enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("production_enabled_by", sa.Integer(), nullable=True),
        sa.Column("production_instance", sa.String(length=16), nullable=True),
        sa.Column("consecutives_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutives_confirmed_by", sa.Integer(), nullable=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_fiscal_profile_tenant"),
    )
    op.create_index("ix_fiscal_profile_tenant_id", "fiscal_profile", ["tenant_id"])
    op.create_table(
        "fiscal_activity",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=6), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_fiscal_activity"),
    )
    op.create_index("ix_fiscal_activity_tenant_id", "fiscal_activity", ["tenant_id"])
    op.create_table(
        "fiscal_credential",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("environment", sa.String(length=12), nullable=False),
        sa.Column("atv_user_enc", sa.Text(), nullable=True),
        sa.Column("atv_password_enc", sa.Text(), nullable=True),
        sa.Column("p12_enc", sa.Text(), nullable=True),
        sa.Column("p12_pin_enc", sa.Text(), nullable=True),
        sa.Column("key_id", sa.String(length=20), nullable=True),
        sa.Column("cert_subject", sa.String(length=300), nullable=True),
        sa.Column("cert_issuer", sa.String(length=300), nullable=True),
        sa.Column("cert_serial", sa.String(length=80), nullable=True),
        sa.Column("cert_not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cert_not_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cert_sha256", sa.String(length=64), nullable=True),
        sa.Column("cert_key_bits", sa.Integer(), nullable=True),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("last_test_detail", sa.String(length=300), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "environment", name="uq_fiscal_credential_env"),
    )
    op.create_index("ix_fiscal_credential_tenant_id", "fiscal_credential", ["tenant_id"])
    op.create_table(
        "reception_mailbox",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("uidvalidity", sa.BigInteger(), nullable=True),
        sa.Column("last_uid", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_result", sa.JSON(), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=64), nullable=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "key", name="uq_reception_mailbox"),
    )
    op.create_index("ix_reception_mailbox_tenant_id", "reception_mailbox", ["tenant_id"])
    op.create_table(
        "reception_message",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mailbox_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("uidvalidity", sa.BigInteger(), nullable=True),
        sa.Column("uid", sa.BigInteger(), nullable=True),
        sa.Column("message_id", sa.String(length=300), nullable=True),
        sa.Column("sender", sa.String(length=300), nullable=True),
        sa.Column("subject", sa.String(length=300), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("raw_eml", sa.LargeBinary(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="procesado"),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["mailbox_id"], ["reception_mailbox.id"], name="fk_reception_message_mailbox"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "mailbox_id", "uidvalidity", "uid", name="uq_reception_message_uid"),
    )
    op.create_index("ix_reception_message_tenant_id", "reception_message", ["tenant_id"])
    op.create_index("ix_reception_message_sha256", "reception_message", ["sha256"])
    op.create_table(
        "bank_instrument",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bank_account_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("last4", sa.String(length=4), nullable=True),
        sa.Column("holder", sa.String(length=120), nullable=True),
        sa.Column("check_from", sa.Integer(), nullable=True),
        sa.Column("check_to", sa.Integer(), nullable=True),
        sa.Column("next_check", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["bank_account_id"], ["bank_account.id"], name="fk_bank_instrument_account"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bank_instrument_tenant_id", "bank_instrument", ["tenant_id"])
    op.create_index("ix_bank_instrument_bank_account_id", "bank_instrument", ["bank_account_id"])
    op.create_table(
        "reception_response",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("received_document_id", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("message_code", sa.String(length=1), nullable=False),
        sa.Column("iva_condition_code", sa.String(length=2), nullable=True),
        sa.Column("activity_code", sa.String(length=10), nullable=True),
        sa.Column("tax_total", sa.Numeric(16, 5), nullable=False, server_default="0"),
        sa.Column("total", sa.Numeric(16, 5), nullable=False, server_default="0"),
        sa.Column("credit_amount", sa.Numeric(16, 5), nullable=True),
        sa.Column("applicable_amount", sa.Numeric(16, 5), nullable=True),
        sa.Column("detail", sa.String(length=160), nullable=True),
        sa.Column("consecutive", sa.String(length=20), nullable=False),
        sa.Column("mode", sa.String(length=12), nullable=False),
        sa.Column("emitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("xml_signed", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="por_enviar"),
        sa.Column("provider_ref", sa.String(length=200), nullable=True),
        sa.Column("hacienda_xml", sa.Text(), nullable=True),
        sa.Column("hacienda_detail", sa.String(length=500), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["received_document_id"], ["received_document.id"], name="fk_reception_response_document"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("received_document_id", "attempt", name="uq_reception_response_attempt"),
        sa.UniqueConstraint("tenant_id", "mode", "consecutive", name="uq_reception_response_consecutive"),
    )
    op.create_index("ix_reception_response_tenant_id", "reception_response", ["tenant_id"])
    op.create_index("ix_reception_response_received_document_id", "reception_response", ["received_document_id"])

    # 2. received_document
    with op.batch_alter_table("received_document") as b:
        b.add_column(sa.Column("doc_type", sa.String(length=4), nullable=False, server_default="FE"))
        b.add_column(sa.Column("schema_version", sa.String(length=5), nullable=True))
        b.add_column(sa.Column("issuer_id_type", sa.String(length=2), nullable=True))
        b.add_column(sa.Column("issuer_email", sa.String(length=200), nullable=True))
        b.add_column(sa.Column("issuer_activity_code", sa.String(length=10), nullable=True))
        b.add_column(sa.Column("receiver_id", sa.String(length=20), nullable=True))
        b.add_column(sa.Column("receiver_ok", sa.Boolean(), nullable=True))
        b.add_column(sa.Column("fx_rate", sa.Numeric(12, 5), nullable=False, server_default="1"))
        b.add_column(sa.Column("other_charges", sa.Numeric(16, 5), nullable=False, server_default="0"))
        b.add_column(sa.Column("iva_total", sa.Numeric(16, 5), nullable=True))
        b.add_column(sa.Column("reference_clave", sa.String(length=50), nullable=True))
        b.add_column(sa.Column("status", sa.String(length=16), nullable=False, server_default="por_revisar"))
        b.add_column(sa.Column("origin", sa.String(length=10), nullable=False, server_default="manual"))
        b.add_column(sa.Column("message_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("pdf_media_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("xml_supplier_response", sa.Text(), nullable=True))
        b.add_column(sa.Column("supplier_hacienda_status", sa.String(length=12), nullable=False, server_default="desconocido"))
        b.add_column(sa.Column("supplier_hacienda_detail", sa.String(length=500), nullable=True))
        b.add_column(sa.Column("credit_amount", sa.Numeric(16, 5), nullable=True))
        b.add_column(sa.Column("applicable_amount", sa.Numeric(16, 5), nullable=True))
        b.add_column(sa.Column("accepted_total", sa.Numeric(16, 5), nullable=True))
        b.add_column(sa.Column("accepted_tax", sa.Numeric(16, 5), nullable=True))
        b.add_column(sa.Column("response_detail", sa.String(length=160), nullable=True))
        b.add_column(sa.Column("deadline", sa.Date(), nullable=True))
        b.add_column(sa.Column("responded_by", sa.Integer(), nullable=True))
        b.add_column(sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("discarded_reason", sa.String(length=200), nullable=True))
        b.add_column(sa.Column("discarded_by", sa.Integer(), nullable=True))
        b.add_column(sa.Column("discarded_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("status_before_discard", sa.String(length=16), nullable=True))
        b.alter_column("iva_condition", type_=sa.String(length=16), existing_type=sa.String(length=12), existing_nullable=False)
        b.create_foreign_key("fk_received_document_message", "reception_message", ["message_id"], ["id"])
        b.create_foreign_key("fk_received_document_pdf", "media", ["pdf_media_id"], ["id"])
        b.create_index("ix_received_document_status", ["status"])
    dedupe_receptions(op.get_bind())
    with op.batch_alter_table("received_document") as b:
        b.create_unique_constraint("uq_received_document_clave", ["tenant_id", "clave"])

    # 3. expense
    with op.batch_alter_table("expense") as b:
        b.add_column(sa.Column("received_document_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("fx_rate", sa.Numeric(12, 5), nullable=False, server_default="1"))
        b.add_column(sa.Column("tax_credit_amount", sa.Numeric(16, 5), nullable=True))
        b.add_column(sa.Column("notes", sa.Text(), nullable=True))
        b.add_column(sa.Column("payment_method", sa.String(length=20), nullable=True))
        b.add_column(sa.Column("purchase_type", sa.String(length=10), nullable=False, server_default="contado"))
        b.add_column(sa.Column("due_date", sa.Date(), nullable=True))
        b.add_column(sa.Column("recurrence_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("recurrence_date", sa.Date(), nullable=True))
        b.add_column(sa.Column("bank_instrument_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("check_number", sa.String(length=20), nullable=True))
        b.alter_column("iva_credit", type_=sa.String(length=16), existing_type=sa.String(length=12), existing_nullable=False)
        b.create_foreign_key("fk_expense_received_document", "received_document", ["received_document_id"], ["id"])
        b.create_foreign_key("fk_expense_recurrence", "recurrence", ["recurrence_id"], ["id"])
        b.create_foreign_key("fk_expense_bank_instrument", "bank_instrument", ["bank_instrument_id"], ["id"])
        b.create_index("ix_expense_received_document_id", ["received_document_id"])
        b.create_unique_constraint("uq_expense_recurrence_date", ["tenant_id", "recurrence_id", "recurrence_date"])

    # 4. resto de columnas
    with op.batch_alter_table("expense_category") as b:
        b.add_column(sa.Column("d151_kind", sa.String(length=24), nullable=True))
    with op.batch_alter_table("recurrence") as b:
        b.add_column(sa.Column("anchor_day", sa.SmallInteger(), nullable=True))
    with op.batch_alter_table("bank_account") as b:
        b.add_column(sa.Column("opening_balance", sa.Numeric(16, 2), nullable=False, server_default="0"))
        b.add_column(sa.Column("opening_date", sa.Date(), nullable=True))
    with op.batch_alter_table("einvoice_document") as b:
        b.add_column(sa.Column("mode", sa.String(length=12), nullable=True))
        b.add_column(sa.Column("emitted_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("last_error", sa.String(length=300), nullable=True))
        b.add_column(sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("hacienda_delivered_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("delivery_failed_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("hacienda_delivery_failed_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("receiver_message", sa.String(length=1), nullable=True))
        b.add_column(sa.Column("receiver_detail", sa.String(length=160), nullable=True))
        b.add_column(sa.Column("receiver_xml", sa.Text(), nullable=True))
        b.add_column(sa.Column("receiver_at", sa.DateTime(timezone=True), nullable=True))

    # 5. relleno
    backfill(op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    with op.batch_alter_table("einvoice_document") as b:
        for c in (
            "receiver_at",
            "receiver_xml",
            "receiver_detail",
            "receiver_message",
            "hacienda_delivery_failed_at",
            "delivery_failed_at",
            "hacienda_delivered_at",
            "delivered_at",
            "last_error",
            "claimed_at",
            "next_attempt_at",
            "emitted_at",
            "mode",
        ):
            b.drop_column(c)
    with op.batch_alter_table("bank_account") as b:
        b.drop_column("opening_date")
        b.drop_column("opening_balance")
    with op.batch_alter_table("recurrence") as b:
        b.drop_column("anchor_day")
    with op.batch_alter_table("expense_category") as b:
        b.drop_column("d151_kind")

    # valores que no caben en el codigo viejo (no tenia parcial: "credito" sobrestimaria el credito)
    for table, col in (("expense", "iva_credit"), ("received_document", "iva_condition")):
        bind.execute(sa.text(f"UPDATE {table} SET {col} = 'credito' WHERE {col} = 'bienes_capital'"))
        bind.execute(sa.text(f"UPDATE {table} SET {col} = 'proporcional' WHERE {col} = 'credito_parcial'"))

    with op.batch_alter_table("expense") as b:
        b.drop_constraint("uq_expense_recurrence_date", type_="unique")
        b.drop_index("ix_expense_received_document_id")
        b.drop_constraint("fk_expense_bank_instrument", type_="foreignkey")
        b.drop_constraint("fk_expense_recurrence", type_="foreignkey")
        b.drop_constraint("fk_expense_received_document", type_="foreignkey")
        b.alter_column("iva_credit", type_=sa.String(length=12), existing_type=sa.String(length=16), existing_nullable=False)
        for c in (
            "check_number",
            "bank_instrument_id",
            "recurrence_date",
            "recurrence_id",
            "due_date",
            "purchase_type",
            "payment_method",
            "notes",
            "tax_credit_amount",
            "fx_rate",
            "received_document_id",
        ):
            b.drop_column(c)

    op.drop_index("ix_reception_response_received_document_id", table_name="reception_response")
    op.drop_index("ix_reception_response_tenant_id", table_name="reception_response")
    op.drop_table("reception_response")

    with op.batch_alter_table("received_document") as b:
        b.drop_constraint("uq_received_document_clave", type_="unique")
        b.drop_index("ix_received_document_status")
        b.drop_constraint("fk_received_document_pdf", type_="foreignkey")
        b.drop_constraint("fk_received_document_message", type_="foreignkey")
        b.alter_column("iva_condition", type_=sa.String(length=12), existing_type=sa.String(length=16), existing_nullable=False)
        for c in (
            "status_before_discard",
            "discarded_at",
            "discarded_by",
            "discarded_reason",
            "responded_at",
            "responded_by",
            "deadline",
            "response_detail",
            "accepted_tax",
            "accepted_total",
            "applicable_amount",
            "credit_amount",
            "supplier_hacienda_detail",
            "supplier_hacienda_status",
            "xml_supplier_response",
            "pdf_media_id",
            "message_id",
            "origin",
            "status",
            "reference_clave",
            "iva_total",
            "other_charges",
            "fx_rate",
            "receiver_ok",
            "receiver_id",
            "issuer_activity_code",
            "issuer_email",
            "issuer_id_type",
            "schema_version",
            "doc_type",
        ):
            b.drop_column(c)

    op.drop_index("ix_bank_instrument_bank_account_id", table_name="bank_instrument")
    op.drop_index("ix_bank_instrument_tenant_id", table_name="bank_instrument")
    op.drop_table("bank_instrument")
    op.drop_index("ix_reception_message_sha256", table_name="reception_message")
    op.drop_index("ix_reception_message_tenant_id", table_name="reception_message")
    op.drop_table("reception_message")
    op.drop_index("ix_reception_mailbox_tenant_id", table_name="reception_mailbox")
    op.drop_table("reception_mailbox")
    op.drop_index("ix_fiscal_credential_tenant_id", table_name="fiscal_credential")
    op.drop_table("fiscal_credential")
    op.drop_index("ix_fiscal_activity_tenant_id", table_name="fiscal_activity")
    op.drop_table("fiscal_activity")
    op.drop_index("ix_fiscal_profile_tenant_id", table_name="fiscal_profile")
    op.drop_table("fiscal_profile")
