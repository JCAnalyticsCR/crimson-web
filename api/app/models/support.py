"""Soporte, mantenimientos y comisiones (sesion 6).

Las tres piezas que Andres pidio en la reunion del 22/09 y que no existian:
- Soporte: los tickets de los clientes. "Es una parte del negocio a la que le estoy dando duro": va a asumir
  el soporte de Hikvision Costa Rica y Latinoamerica, nivel 1 a 3, y necesita que todo quede trazado aqui.
- Mantenimientos: "este cliente requiere mantenimiento cada 6 meses". El worker abre el ticket solo.
- Comisiones: "la mayoria de mis negocios se van a basar en comisionar". Se calculan sobre lo COBRADO,
  no sobre lo facturado, que es la unica plata que existe de verdad.

Ojo con el nombre: Ticket ya existe y es la entrada de un evento. El del cliente es SupportTicket.
"""

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base
from .base import TenantMixin, TimestampMixin

TICKET_KINDS = ("soporte", "garantia", "mantenimiento", "visita", "instalacion", "consulta")
TICKET_STATES = ("nuevo", "asignado", "en_proceso", "esperando_cliente", "resuelto", "cerrado")
PRIORITIES = ("baja", "media", "alta", "critica")
# Nivel de soporte, como lo planteo para Hikvision: 1 atiende, 2 diagnostica, 3 escala al fabricante.
LEVELS = (1, 2, 3)


class SupportTicket(TenantMixin, TimestampMixin, Base):
    """Un caso de un cliente: falla, garantia, mantenimiento o consulta."""

    __tablename__ = "support_ticket"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.id"), index=True)
    contact: Mapped[dict] = mapped_column(JSON, default=dict)  # quien reporto, si no tiene ficha
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("customer_asset.id"))  # el equipo que falla
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id"))
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("maintenance_contract.id"))
    work_order_id: Mapped[int | None] = mapped_column(ForeignKey("work_order.id"))  # si hubo que ir al sitio
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoice.id"))  # si se cobro

    kind: Mapped[str] = mapped_column(String(20), default="soporte", index=True)
    channel: Mapped[str | None] = mapped_column(String(20))  # whatsapp, correo, llamada, presencial, portal
    level: Mapped[int] = mapped_column(Integer, default=1)
    subject: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(10), default="media", index=True)
    status: Mapped[str] = mapped_column(String(20), default="nuevo", index=True)

    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("user.id"), index=True)
    opened_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_reply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    hours: Mapped[float] = mapped_column(Numeric(8, 2), default=0)
    billable: Mapped[bool] = mapped_column(Boolean, default=False)  # dentro de garantia o contrato = no se cobra
    amount: Mapped[float] = mapped_column(Numeric(16, 2), default=0)  # lo que costo el soporte, si se cobra
    solution: Mapped[str | None] = mapped_column(Text)
    photos: Mapped[list] = mapped_column(JSON, default=list)
    tags: Mapped[list] = mapped_column(JSON, default=list)

    notes: Mapped[list["SupportNote"]] = relationship(cascade="all, delete-orphan", order_by="SupportNote.id", lazy="selectin")


class SupportNote(TenantMixin, TimestampMixin, Base):
    """Cada movimiento del ticket. Las internas no las ve el cliente si algun dia hay portal."""

    __tablename__ = "support_note"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_ticket.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    body: Mapped[str] = mapped_column(Text)
    internal: Mapped[bool] = mapped_column(Boolean, default=False)
    photos: Mapped[list] = mapped_column(JSON, default=list)


class MaintenanceContract(TenantMixin, TimestampMixin, Base):
    """Mantenimiento preventivo o soporte recurrente: cada cuantos meses toca y cuando es la proxima."""

    __tablename__ = "maintenance_contract"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id"))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="mantenimiento")  # mantenimiento | soporte | renting | licencia
    every_months: Mapped[int] = mapped_column(Integer, default=6)
    next_date: Mapped[date | None] = mapped_column(Date, index=True)
    last_done: Mapped[date | None] = mapped_column(Date)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Numeric(16, 2), default=0)  # lo que se cobra por visita o periodo
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    scope: Mapped[str | None] = mapped_column(Text)  # que incluye
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text)


class CommissionRule(TenantMixin, TimestampMixin, Base):
    """Cuanto comisiona cada quien. Sin usuario = regla general de la empresa."""

    __tablename__ = "commission_rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="Comisión")
    base: Mapped[str] = mapped_column(String(10), default="venta")  # venta (sobre lo cobrado) | margen (sobre la utilidad)
    percent: Mapped[float] = mapped_column(Numeric(6, 2), default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text)


class Commission(TenantMixin, TimestampMixin, Base):
    """Comision devengada. Nace cuando entra el pago, no cuando se factura: "venta cobrada -> comision"."""

    __tablename__ = "commission"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoice.id"), index=True)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payment.id"), index=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("commission_rule.id"))
    base: Mapped[str] = mapped_column(String(10), default="venta")
    base_amount: Mapped[float] = mapped_column(Numeric(16, 2), default=0)  # sobre que se calculo
    percent: Mapped[float] = mapped_column(Numeric(6, 2), default=0)
    amount: Mapped[float] = mapped_column(Numeric(16, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    earned_on: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(12), default="pendiente", index=True)  # pendiente | aprobada | pagada | anulada
    paid_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
