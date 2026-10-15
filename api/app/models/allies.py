"""Aliados por oportunidad (y su proyecto): quien participa ademas de Crimson y que se le pidio.

Andres: "Nodo contrata a Crimson, y Crimson contrata a Hauset". El contratante sigue siendo el customer_id de la
oportunidad; aqui viven las demas empresas (un cliente o un proveedor existente), su papel, su alcance, sus
contactos por funcion y las solicitudes de costo. Nada de esto sale al portal del cliente.

Empresa aliada (reunion 13/10, 10:47-31:53): entidad propia (AllyCompany). Sus usuarios (roles aliado y
tecnico_aliado) viven en su propio portal (/aliado, routers/ally_portal.py) y solo ven lo que tienen EN COMUN con
Crimson (services/ally_scope.py). El dinero que recibe el aliado (comisiones, pagos acordados) es del frente economia.
"""

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy import true as sa_true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base
from .base import TenantMixin, TimestampMixin

ALLY_ROLES = ("contratante", "referido", "subcontratista", "suministro", "configuracion", "software")
CONTACT_FUNCTIONS = ("comercial", "tecnico", "aprobador", "pagos")
COST_STATES = ("solicitado", "recibido", "aprobado", "rechazado")
COST_OPEN = ("solicitado", "recibido")  # pendiente: falta que llegue o que alguien la apruebe
COST_SOURCES = ("crimson", "aliado")  # crimson: Crimson lo pidio | aliado: el aliado lo envio por iniciativa
PLAN_MODES = ("fija", "por_usuario")


class AllyCompany(TenantMixin, TimestampMixin, Base):
    """Empresa aliada: socio comercial que usa la plataforma (sus usuarios entran al portal de aliados).

    Sin papelera: no se borra, se desactiva (active=False). La suspension (mensualidad atrasada u otra razon) va
    aparte en suspended_at: el aliado ve su suscripcion y sus facturas, nada mas. customer_id es el cliente al que
    Crimson le factura (mensualidad); supplier_id el proveedor al que Crimson le paga."""

    __tablename__ = "ally_company"
    __table_args__ = (UniqueConstraint("tenant_id", "tax_id", name="uq_ally_company_tax"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    legal_name: Mapped[str | None] = mapped_column(String(200))
    tax_id: Mapped[str | None] = mapped_column(String(30), index=True)  # cedula juridica (los NULL no chocan)
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))
    contacts: Mapped[dict | None] = mapped_column(JSON, default=dict)  # {comercial|tecnico|aprobador|pagos: {name, email, phone}}
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true(), index=True)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_reason: Mapped[str | None] = mapped_column(String(240))  # se le muestra al aliado
    markup_pct: Mapped[float | None] = mapped_column(Numeric(6, 2))  # recargo propio sobre costo; NULL = el de la empresa
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.id", ondelete="RESTRICT", name="fk_ally_company_customer"), index=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("supplier.id", ondelete="RESTRICT", name="fk_ally_company_supplier"))
    max_users: Mapped[int | None] = mapped_column(Integer)  # usuarios activos + invitaciones vivas; NULL = sin tope
    plan_amount: Mapped[float | None] = mapped_column(Numeric(16, 2))  # mensualidad sin IVA (por empresa o por usuario)
    plan_currency: Mapped[str] = mapped_column(String(3), default="CRC", server_default="CRC")
    plan_mode: Mapped[str] = mapped_column(String(12), default="fija", server_default="fija")  # fija | por_usuario
    plan_since: Mapped[date | None] = mapped_column(Date)
    recurrence_id: Mapped[int | None] = mapped_column(ForeignKey("recurrence.id", ondelete="SET NULL", name="fk_ally_company_recurrence"))
    notes: Mapped[str | None] = mapped_column(Text)  # interno: nunca sale al portal
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_ally_company_created_by"))

    @property
    def state(self) -> str:
        if not self.active:
            return "inactivo"
        return "suspendido" if self.suspended_at else "activo"


class AllyProductPrice(TenantMixin, TimestampMixin, Base):
    """Precio fijo de aliado (sin IVA, moneda del producto por defecto): para una empresa o general (NULL = todos)."""

    __tablename__ = "ally_product_price"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE", name="fk_ally_pp_product"), index=True)
    ally_company_id: Mapped[int | None] = mapped_column(ForeignKey("ally_company.id", ondelete="CASCADE", name="fk_ally_pp_company"), index=True)
    price: Mapped[float] = mapped_column(Numeric(14, 5))
    currency: Mapped[str] = mapped_column(String(3))
    note: Mapped[str | None] = mapped_column(String(200))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_ally_pp_updated_by"))


# Un solo precio por producto y alcance (general = 0). Indice por expresion (SQLite >= 3.9 y Postgres); ademas upsert.
Index(
    "uq_ally_product_price_scope",
    AllyProductPrice.tenant_id,
    AllyProductPrice.product_id,
    func.coalesce(AllyProductPrice.ally_company_id, 0),
    unique=True,
)


class AllyParticipation(TenantMixin, TimestampMixin, Base):
    """Una empresa que participa en la oportunidad. Al crear el proyecto desde la cotizacion queda ligada tambien
    al proyecto (project_id): es la misma participacion, no una copia."""

    __tablename__ = "ally_participation"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunity.id", ondelete="CASCADE", name="fk_ally_opportunity"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id", ondelete="SET NULL", name="fk_ally_project"), index=True)
    # la empresa es una empresa aliada, un cliente o un proveedor existente (una sola); name es la foto del nombre.
    # Las de cliente/proveedor son de antes: no dan acceso al portal de aliados hasta "Convertir en aliado"
    ally_company_id: Mapped[int | None] = mapped_column(ForeignKey("ally_company.id", ondelete="RESTRICT", name="fk_ally_participation_company"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.id", name="fk_ally_customer"))
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("supplier.id", name="fk_ally_supplier"))
    # el aliado la ve en su portal; apagarla le quita el acceso a la oportunidad (y su proyecto) al instante
    visible_to_ally: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true())
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
    # respuesta del aliado desde su portal: desglose [{concept, quantity, unit, unit_price}] y quien/cuando
    lines: Mapped[list | None] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(10), default="crimson", server_default="crimson")  # crimson | aliado
    responded_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_costreq_responded_by"))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    participation: Mapped[AllyParticipation] = relationship(back_populates="requests")
