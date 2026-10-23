"""Ventas etapa 3: metas, agenda y tareas, kits de cotizacion, plantillas de mensaje y enlace de aceptacion en linea."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy import true as sa_true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base
from .base import TenantMixin, TimestampMixin


class SalesGoal(TenantMixin, TimestampMixin, Base):
    """Meta mensual de un vendedor en colones. month = primer dia del mes. Un mes sin meta propia usa la ultima meta
    anterior del vendedor (se arrastra) y, si no hay, la meta por defecto de Ajustes -> Ventas."""

    __tablename__ = "sales_goal"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "month", name="uq_sales_goal_month"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE", name="fk_sales_goal_user"), index=True)
    month: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    set_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_sales_goal_set_by"))


class SalesTask(TenantMixin, TimestampMixin, Base):
    """Llamada, visita o recordatorio de un vendedor, ligado a un cliente, una oportunidad o una cotizacion.
    due_at se guarda en UTC; el portal lo escribe y lo muestra en hora de Costa Rica. kind "facturar" lo crea la
    aceptacion en linea de una cotizacion."""

    __tablename__ = "sales_task"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(14), default="llamada")  # llamada | visita | recordatorio | facturar
    title: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    assigned_to: Mapped[int] = mapped_column(ForeignKey("user.id", name="fk_sales_task_assigned"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.id", ondelete="SET NULL", name="fk_sales_task_customer"), index=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunity.id", ondelete="SET NULL", name="fk_sales_task_opportunity"), index=True)
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("quote.id", ondelete="SET NULL", name="fk_sales_task_quote"), index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_sales_task_created_by"))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_sales_task_done_by"))


class QuoteKit(TenantMixin, TimestampMixin, Base):
    """Kit de cotizacion que arma el admin ("4 camaras + grabador + disco + instalacion"): productos del catalogo con
    cantidades. Al insertarlo los precios salen del catalogo de ese momento (nunca se guardan en el kit)."""

    __tablename__ = "quote_kit"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true())

    lines: Mapped[list["QuoteKitLine"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="QuoteKitLine.position")


class QuoteKitLine(Base):
    __tablename__ = "quote_kit_line"

    id: Mapped[int] = mapped_column(primary_key=True)
    kit_id: Mapped[int] = mapped_column(ForeignKey("quote_kit.id", ondelete="CASCADE", name="fk_quote_kit_line_kit"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE", name="fk_quote_kit_line_product"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=1)
    position: Mapped[int] = mapped_column(Integer, default=0)


class MessageTemplate(TenantMixin, TimestampMixin, Base):
    """Texto de correo o WhatsApp para enviar una cotizacion, con variables {cliente}, {numero}, {total}, {enlace},
    {vendedor} y {validez}. Va siempre como texto (nunca HTML)."""

    __tablename__ = "message_template"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    channel: Mapped[str] = mapped_column(String(10), default="correo")  # correo | whatsapp
    body: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true())


class QuoteLink(TenantMixin, TimestampMixin, Base):
    """Enlace publico de una cotizacion para verla y aceptarla sin iniciar sesion. token aleatorio (32 bytes), uno vivo
    por cotizacion; vence con la validez de la cotizacion. Guarda la primera y la ultima apertura."""

    __tablename__ = "quote_link"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quote.id", ondelete="CASCADE", name="fk_quote_link_quote"), index=True)
    token: Mapped[str] = mapped_column(String(80), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    open_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
