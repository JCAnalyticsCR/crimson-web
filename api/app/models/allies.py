"""Aliados por oportunidad (y su proyecto): quien participa ademas de Crimson y que se le pidio.

Andres: "Nodo contrata a Crimson, y Crimson contrata a Hauset". El contratante sigue siendo el customer_id de la
oportunidad; aqui viven las demas empresas (un cliente o un proveedor existente), su papel, su alcance, sus
contactos por funcion y las solicitudes de costo. Nada de esto sale al portal del cliente.
"""

from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base
from .base import TenantMixin, TimestampMixin

ALLY_ROLES = ("contratante", "referido", "subcontratista", "suministro", "configuracion", "software")
CONTACT_FUNCTIONS = ("comercial", "tecnico", "aprobador", "pagos")
COST_STATES = ("solicitado", "recibido", "aprobado", "rechazado")
COST_OPEN = ("solicitado", "recibido")  # pendiente: falta que llegue o que alguien la apruebe


class AllyParticipation(TenantMixin, TimestampMixin, Base):
    """Una empresa que participa en la oportunidad. Al crear el proyecto desde la cotizacion queda ligada tambien
    al proyecto (project_id): es la misma participacion, no una copia."""

    __tablename__ = "ally_participation"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunity.id", ondelete="CASCADE", name="fk_ally_opportunity"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id", ondelete="SET NULL", name="fk_ally_project"), index=True)
    # la empresa es un cliente o un proveedor existente; name es la foto del nombre al ligarla
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.id", name="fk_ally_customer"))
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("supplier.id", name="fk_ally_supplier"))
    name: Mapped[str] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(20), default="subcontratista")
    scope: Mapped[str | None] = mapped_column(Text)  # alcance asignado
    scope_lines: Mapped[list] = mapped_column(JSON, default=list)  # partidas de la cotizacion (nombres)
    contacts: Mapped[dict] = mapped_column(JSON, default=dict)  # {comercial|tecnico|aprobador|pagos: {name, email, phone}}
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_ally_created_by"))

    requests: Mapped[list["AllyCostRequest"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="AllyCostRequest.id", back_populates="participation"
    )


class AllyCostRequest(TenantMixin, TimestampMixin, Base):
    """Solicitud de costo a un aliado: que se pidio, quien la persigue y para cuando. Vencida o sin respuesta
    cuenta como pendiente en la oportunidad y en el inicio."""

    __tablename__ = "ally_cost_request"

    id: Mapped[int] = mapped_column(primary_key=True)
    participation_id: Mapped[int] = mapped_column(ForeignKey("ally_participation.id", ondelete="CASCADE", name="fk_costreq_participation"), index=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunity.id", ondelete="CASCADE", name="fk_costreq_opportunity"), index=True)
    what: Mapped[str] = mapped_column(String(300))
    responsible_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_costreq_responsible"), index=True)
    due_date: Mapped[date | None] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(12), default="solicitado", index=True)
    amount: Mapped[float | None] = mapped_column(Numeric(16, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    valid_until: Mapped[date | None] = mapped_column(Date)  # vigencia de la oferta del aliado
    exclusions: Mapped[str | None] = mapped_column(Text)
    attachments: Mapped[list] = mapped_column(JSON, default=list)  # [{id, url, filename}] de media
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_costreq_created_by"))

    participation: Mapped[AllyParticipation] = relationship(back_populates="requests")
