"""aliados (revision 2 de Andres, 30/10): alcances y responsables, solicitud de precio separada de la oferta,
acuerdos aceptados con versiones, transparencia por niveles, relaciones concretas y servicio adicional

- scope_item (nueva): partidas de alcance por oportunidad (o por levantamiento sin oportunidad): tipo, que se necesita,
  cantidad/unidad, quien suministra y quien ejecuta (Crimson, una participacion o por asignar), estado y si se
  comparte con los aliados.
- ally_agreement (nueva): la foto de cada oferta aceptada, con versiones (precio, moneda, vigencia, condiciones,
  exclusiones, desglose) y que cambio en cada una.
- ally_cost_request: la peticion (alcance, sitio, inclusiones, exclusiones, partidas, adjuntos, como respondera), la
  entrega (sent_at, seen_at, seen_by), las condiciones de la oferta y la decision (decided_at/by, nota).
  Estados: solicitado -> enviado, recibido -> respondido, aprobado -> aceptado, rechazado igual. sent_at = created_at
  (todas las de antes ya estaban enviadas). Las de una participacion sin empresa aliada responden "por correo".
  Cada aprobada con monto queda como acuerdo version 1, NO compartido con los demas aliados (se acepto antes de que
  existiera la transparencia por niveles: compartirla es decision de Crimson).
- ally_participation: is_origin (Aliado comercial) e is_executor (Responsable de ejecucion). La primera participacion
  "referido" de cada oportunidad queda como aliado comercial; nadie queda como responsable de ejecucion (= Crimson).
- opportunity.ally_open_costs: "trabajar con costos abiertos" (apagado).
- ally_company: extra_service, extra_service_accepted_at/by (punto 13). Sin relleno: una mensualidad de antes queda
  como estaba (sin servicio definido) y la pantalla lo advierte.

downgrade: devuelve los estados viejos (borrador/enviado/visto -> solicitado, respondido -> recibido, aceptado ->
aprobado) y borra lo nuevo (las versiones de acuerdo y los alcances se pierden).

Revision ID: b7d4e1c9a382
Revises: d4b7e1a9c352
Create Date: 2026-10-30 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7d4e1c9a382"
down_revision: str | None = "d4b7e1a9c352"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATES_UP = {"solicitado": "enviado", "recibido": "respondido", "aprobado": "aceptado"}
STATES_DOWN = {"borrador": "solicitado", "enviado": "solicitado", "visto": "solicitado", "respondido": "recibido", "aceptado": "aprobado"}


def upgrade() -> None:
    op.create_table(
        "scope_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=True),
        sa.Column("survey_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("description", sa.String(length=400), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 2), nullable=True),
        sa.Column("unit", sa.String(length=12), nullable=True),
        sa.Column("supplier_kind", sa.String(length=12), server_default="por_asignar", nullable=False),
        sa.Column("supplier_participation_id", sa.Integer(), nullable=True),
        sa.Column("executor_kind", sa.String(length=12), server_default="por_asignar", nullable=False),
        sa.Column("executor_participation_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="por_confirmar", nullable=False),
        sa.Column("shared", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunity.id"], name="fk_scope_item_opportunity", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["survey_id"], ["survey.id"], name="fk_scope_item_survey", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["supplier_participation_id"], ["ally_participation.id"], name="fk_scope_item_supplier", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["executor_participation_id"], ["ally_participation.id"], name="fk_scope_item_executor", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["confirmed_by"], ["user.id"], name="fk_scope_item_confirmed_by"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], name="fk_scope_item_created_by"),
        sa.ForeignKeyConstraint(["updated_by"], ["user.id"], name="fk_scope_item_updated_by"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("scope_item") as b:
        b.create_index("ix_scope_item_tenant_id", ["tenant_id"])
        b.create_index("ix_scope_item_opportunity_id", ["opportunity_id"])
        b.create_index("ix_scope_item_survey_id", ["survey_id"])

    op.create_table(
        "ally_agreement",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("participation_id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("what", sa.String(length=300), nullable=False),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("amount", sa.Numeric(16, 2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("conditions", sa.Text(), nullable=True),
        sa.Column("exclusions", sa.Text(), nullable=True),
        sa.Column("lines", sa.JSON(), nullable=True),
        sa.Column("shared", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=True),
        sa.Column("note", sa.String(length=400), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunity.id"], name="fk_agreement_opportunity", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["participation_id"], ["ally_participation.id"], name="fk_agreement_participation", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["request_id"], ["ally_cost_request.id"], name="fk_agreement_request", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], name="fk_agreement_created_by"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", "version", name="uq_ally_agreement_version"),
    )
    with op.batch_alter_table("ally_agreement") as b:
        b.create_index("ix_ally_agreement_tenant_id", ["tenant_id"])
        b.create_index("ix_ally_agreement_opportunity_id", ["opportunity_id"])
        b.create_index("ix_ally_agreement_participation_id", ["participation_id"])
        b.create_index("ix_ally_agreement_request_id", ["request_id"])
        b.create_index("ix_ally_agreement_status", ["status"])

    with op.batch_alter_table("ally_cost_request") as b:
        b.add_column(sa.Column("scope", sa.Text(), nullable=True))
        b.add_column(sa.Column("site", sa.String(length=300), nullable=True))
        b.add_column(sa.Column("inclusions", sa.Text(), nullable=True))
        b.add_column(sa.Column("req_exclusions", sa.Text(), nullable=True))
        b.add_column(sa.Column("req_lines", sa.JSON(), nullable=True))
        b.add_column(sa.Column("req_attachments", sa.JSON(), nullable=True))
        b.add_column(sa.Column("response_channel", sa.String(length=12), server_default="portal", nullable=False))
        b.add_column(sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("seen_by", sa.Integer(), nullable=True))
        b.add_column(sa.Column("conditions", sa.Text(), nullable=True))
        b.add_column(sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("decided_by", sa.Integer(), nullable=True))
        b.add_column(sa.Column("decision_note", sa.String(length=400), nullable=True))
        b.create_foreign_key("fk_costreq_seen_by", "user", ["seen_by"], ["id"])
        b.create_foreign_key("fk_costreq_decided_by", "user", ["decided_by"], ["id"])

    with op.batch_alter_table("ally_participation") as b:
        b.add_column(sa.Column("is_origin", sa.Boolean(), server_default=sa.false(), nullable=False))
        b.add_column(sa.Column("is_executor", sa.Boolean(), server_default=sa.false(), nullable=False))

    with op.batch_alter_table("opportunity") as b:
        b.add_column(sa.Column("ally_open_costs", sa.Boolean(), server_default=sa.false(), nullable=False))

    with op.batch_alter_table("ally_company") as b:
        b.add_column(sa.Column("extra_service", sa.Text(), nullable=True))
        b.add_column(sa.Column("extra_service_accepted_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("extra_service_accepted_by", sa.String(length=200), nullable=True))

    _backfill()


def _backfill() -> None:
    bind = op.get_bind()
    req = sa.table(
        "ally_cost_request",
        sa.column("id", sa.Integer),
        sa.column("tenant_id", sa.Integer),
        sa.column("participation_id", sa.Integer),
        sa.column("opportunity_id", sa.Integer),
        sa.column("what", sa.String),
        sa.column("status", sa.String),
        sa.column("amount", sa.Numeric),
        sa.column("currency", sa.String),
        sa.column("valid_until", sa.Date),
        sa.column("exclusions", sa.Text),
        sa.column("lines", sa.JSON),
        sa.column("created_by", sa.Integer),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("sent_at", sa.DateTime(timezone=True)),
        sa.column("decided_at", sa.DateTime(timezone=True)),
        sa.column("response_channel", sa.String),
    )
    part = sa.table(
        "ally_participation",
        sa.column("id", sa.Integer),
        sa.column("opportunity_id", sa.Integer),
        sa.column("ally_company_id", sa.Integer),
        sa.column("role", sa.String),
        sa.column("is_origin", sa.Boolean),
    )
    agr = sa.table(
        "ally_agreement",
        sa.column("tenant_id", sa.Integer),
        sa.column("opportunity_id", sa.Integer),
        sa.column("participation_id", sa.Integer),
        sa.column("request_id", sa.Integer),
        sa.column("version", sa.Integer),
        sa.column("status", sa.String),
        sa.column("what", sa.String),
        sa.column("amount", sa.Numeric),
        sa.column("currency", sa.String),
        sa.column("valid_until", sa.Date),
        sa.column("exclusions", sa.Text),
        sa.column("lines", sa.JSON),
        sa.column("shared", sa.Boolean),
        sa.column("changes", sa.JSON),
        sa.column("created_by", sa.Integer),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    for old, new in STATES_UP.items():
        bind.execute(req.update().where(req.c.status == old).values(status=new))
    # todas las de antes ya le habian llegado al aliado (no existia el borrador)
    bind.execute(req.update().where(req.c.sent_at.is_(None)).values(sent_at=req.c.created_at))
    bind.execute(req.update().where(req.c.status.in_(("aceptado", "rechazado"))).values(decided_at=req.c.updated_at))
    sin_portal = sa.select(part.c.id).where(part.c.ally_company_id.is_(None))
    bind.execute(req.update().where(req.c.participation_id.in_(sin_portal)).values(response_channel="correo"))

    rows = bind.execute(
        sa.select(
            req.c.id, req.c.tenant_id, req.c.participation_id, req.c.opportunity_id, req.c.what, req.c.amount, req.c.currency,
            req.c.valid_until, req.c.exclusions, req.c.lines, req.c.created_by, req.c.updated_at,
        ).where(req.c.status == "aceptado", req.c.amount.is_not(None))
    ).fetchall()  # fmt: skip
    for r in rows:
        bind.execute(
            agr.insert().values(
                tenant_id=r.tenant_id,
                opportunity_id=r.opportunity_id,
                participation_id=r.participation_id,
                request_id=r.id,
                version=1,
                status="vigente",
                what=r.what,
                amount=r.amount,
                currency=r.currency or "CRC",
                valid_until=r.valid_until,
                exclusions=r.exclusions,
                lines=r.lines or [],
                shared=False,
                changes=["aceptada antes de la revisión 2 (no se comparte con los demás aliados hasta que Crimson lo decida)"],
                created_by=r.created_by,
                created_at=r.updated_at,
                updated_at=r.updated_at,
            )
        )

    # la primera participacion "referido" de cada oportunidad queda como Aliado comercial
    ids = [x[0] for x in bind.execute(sa.select(sa.func.min(part.c.id)).where(part.c.role == "referido").group_by(part.c.opportunity_id)).fetchall()]
    if ids:
        bind.execute(part.update().where(part.c.id.in_(ids)).values(is_origin=True))


def downgrade() -> None:
    bind = op.get_bind()
    req = sa.table("ally_cost_request", sa.column("status", sa.String))
    for new, old in STATES_DOWN.items():
        bind.execute(req.update().where(req.c.status == new).values(status=old))

    with op.batch_alter_table("ally_company") as b:
        b.drop_column("extra_service_accepted_by")
        b.drop_column("extra_service_accepted_at")
        b.drop_column("extra_service")
    with op.batch_alter_table("opportunity") as b:
        b.drop_column("ally_open_costs")
    with op.batch_alter_table("ally_participation") as b:
        b.drop_column("is_executor")
        b.drop_column("is_origin")
    with op.batch_alter_table("ally_cost_request") as b:
        b.drop_constraint("fk_costreq_decided_by", type_="foreignkey")
        b.drop_constraint("fk_costreq_seen_by", type_="foreignkey")
        for c in (
            "decision_note", "decided_by", "decided_at", "conditions", "seen_by", "seen_at", "sent_at", "response_channel",
            "req_attachments", "req_lines", "req_exclusions", "inclusions", "site", "scope",
        ):  # fmt: skip
            b.drop_column(c)
    with op.batch_alter_table("ally_agreement") as b:
        for ix in (
            "ix_ally_agreement_status",
            "ix_ally_agreement_request_id",
            "ix_ally_agreement_participation_id",
            "ix_ally_agreement_opportunity_id",
            "ix_ally_agreement_tenant_id",
        ):
            b.drop_index(ix)
    op.drop_table("ally_agreement")
    with op.batch_alter_table("scope_item") as b:
        for ix in ("ix_scope_item_survey_id", "ix_scope_item_opportunity_id", "ix_scope_item_tenant_id"):
            b.drop_index(ix)
    op.drop_table("scope_item")
