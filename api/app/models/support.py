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

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy import false as sa_false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base
from .base import ArchiveMixin, TenantMixin, TimestampMixin

TICKET_KINDS = ("soporte", "garantia", "mantenimiento", "visita", "instalacion", "consulta")
TICKET_STATES = ("nuevo", "asignado", "en_proceso", "esperando_cliente", "resuelto", "cerrado")
PRIORITIES = ("baja", "media", "alta", "critica")
# Nivel de soporte, como lo planteo para Hikvision: 1 atiende, 2 diagnostica, 3 escala al fabricante.
LEVELS = (1, 2, 3)


class SupportTicket(ArchiveMixin, TenantMixin, TimestampMixin, Base):
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
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # limite de primera respuesta
    resolve_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # limite de resolucion
    # foto del SLA que aplico al abrir: horas, origen (contrato/empresa/defecto) y avisos ya enviados
    sla: Mapped[dict | None] = mapped_column(JSON)
    first_reply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    hours: Mapped[float] = mapped_column(Numeric(8, 2), default=0)
    billable: Mapped[bool] = mapped_column(Boolean, default=False)  # dentro de garantia o contrato = no se cobra
    amount: Mapped[float] = mapped_column(Numeric(16, 2), default=0)  # lo que costo el soporte, si se cobra
    solution: Mapped[str | None] = mapped_column(Text)
    photos: Mapped[list] = mapped_column(JSON, default=list)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    # proximo paso del seguimiento: que hay que hacer y para cuando (lo que Andres llama "dar los seguimientos")
    next_step: Mapped[str | None] = mapped_column(String(200))
    next_step_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # ticket asignado a una empresa aliada (lo atiende desde su portal); NULL = de Crimson
    ally_company_id: Mapped[int | None] = mapped_column(ForeignKey("ally_company.id", ondelete="RESTRICT", name="fk_support_ticket_ally_company"), index=True)

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
    # nota interna compartida con la empresa aliada del ticket (el cliente final sigue sin verla)
    share_ally: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false())


class MaintenanceContract(ArchiveMixin, TenantMixin, TimestampMixin, Base):
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
    # SLA propio del contrato {"critica": {"respuesta": h, "resolucion": h}, ...}; None = el de la empresa
    sla: Mapped[dict | None] = mapped_column(JSON)
    # lo ejecuta una empresa aliada: sus tickets de mantenimiento nacen asignados a ella
    ally_company_id: Mapped[int | None] = mapped_column(ForeignKey("ally_company.id", ondelete="RESTRICT", name="fk_contract_ally_company"), index=True)


# ---------- Comisiones v2 (economia): libro unico de lo que la empresa le debe a un vendedor o a un aliado ----------
BENEFICIARIES = ("vendedor", "aliado")  # vendedor = cualquier usuario interno
RULE_KINDS = ("comision", "bono_fin", "bono_anticipo")
ENTRY_KINDS = ("comision", "bono", "pago_acordado", "ajuste")
TRIGGERS = ("cobro", "cotizacion_aceptada", "proyecto_completado")  # + "manual" solo en movimientos
ENTRY_STATES = ("en_espera", "pendiente", "aprobada", "pagada", "anulada")
TRANSITIONS = {
    "en_espera": {"anulada"},  # sale de espera solo con recalculo
    "pendiente": {"aprobada", "pagada", "anulada"},  # pendiente -> pagada directo (test_session6)
    "aprobada": {"pagada", "pendiente", "anulada"},
    "pagada": set(),  # una pagada no se toca: se corrige con un ajuste
    "anulada": set(),
}
DEAL_KINDS = ("venta", "proyecto")  # Opportunity.kind
SOLUTIONS = ("cctv", "redes", "acceso", "asistencia", "ups", "cableado", "anpr", "otro")  # = SURVEY_KINDS


class CommissionRule(TenantMixin, TimestampMixin, Base):
    """Cuanto gana cada quien y cuando. Vendedor sin persona = regla general (solo alcanza a los roles de
    Comisiones -> Configuracion). Aliado sin empresa = todos los aliados; con participation_id = el acuerdo de UN
    proyecto con ese aliado, que manda sobre las generales (services/commissions.best_rule)."""

    __tablename__ = "commission_rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="Comisión")
    base: Mapped[str] = mapped_column(String(10), default="venta")  # venta (sobre lo cobrado) | margen (sobre la utilidad)
    percent: Mapped[float] = mapped_column(Numeric(6, 2), default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text)
    beneficiary: Mapped[str] = mapped_column(String(10), default="vendedor", server_default="vendedor")  # vendedor | aliado
    ally_company_id: Mapped[int | None] = mapped_column(ForeignKey("ally_company.id", name="fk_crule_ally_company"), index=True)
    participation_id: Mapped[int | None] = mapped_column(ForeignKey("ally_participation.id", ondelete="CASCADE", name="fk_crule_participation"), index=True)
    kind: Mapped[str] = mapped_column(String(14), default="comision", server_default="comision")  # comision | bono_fin | bono_anticipo
    trigger: Mapped[str] = mapped_column(String(20), default="cobro", server_default="cobro")  # cuando se devenga
    deal_kind: Mapped[str | None] = mapped_column(String(12))  # venta | proyecto | None = cualquiera
    solution: Mapped[str | None] = mapped_column(String(20))  # SOLUTIONS | None = cualquiera
    fixed_amount: Mapped[float | None] = mapped_column(Numeric(16, 2))  # monto fijo (excluyente con percent > 0)
    fixed_currency: Mapped[str | None] = mapped_column(String(3))
    committed_date: Mapped[date | None] = mapped_column(Date)  # bono_anticipo: fecha comprometida de entrega
    early_days: Mapped[int | None] = mapped_column(Integer)  # bono_anticipo: dias minimos de anticipacion (None = 1)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)


class Commission(TenantMixin, TimestampMixin, Base):
    """Movimiento del libro: comision, bono, pago acordado o ajuste de un vendedor (user_id) o de una empresa aliada
    (ally_company_id). Nace con el evento de su regla ("venta cobrada -> comision", 22/09) y pasa por la bandeja.
    Nunca se borra: se anula o se revierte con un ajuste. source_key hace idempotente cada devengo."""

    __tablename__ = "commission"
    __table_args__ = (UniqueConstraint("tenant_id", "source_key", name="uq_commission_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), index=True)  # null en movimientos de aliado
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoice.id"), index=True)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payment.id"), index=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("commission_rule.id"))
    base: Mapped[str] = mapped_column(String(10), default="venta")
    base_amount: Mapped[float] = mapped_column(Numeric(16, 2), default=0)  # sobre que se calculo
    percent: Mapped[float] = mapped_column(Numeric(6, 2), default=0)
    amount: Mapped[float] = mapped_column(Numeric(16, 2), default=0)  # negativo solo en kind "ajuste"
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    earned_on: Mapped[date] = mapped_column(Date, index=True)
    # en_espera | pendiente | aprobada | pagada | anulada (TRANSITIONS)
    status: Mapped[str] = mapped_column(String(12), default="pendiente", index=True)
    paid_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    beneficiary: Mapped[str] = mapped_column(String(10), default="vendedor", server_default="vendedor")
    ally_company_id: Mapped[int | None] = mapped_column(ForeignKey("ally_company.id", name="fk_comm_ally_company"), index=True)
    participation_id: Mapped[int | None] = mapped_column(ForeignKey("ally_participation.id", ondelete="SET NULL", name="fk_comm_participation"), index=True)
    kind: Mapped[str] = mapped_column(String(14), default="comision", server_default="comision")  # ENTRY_KINDS
    trigger: Mapped[str] = mapped_column(String(20), default="cobro", server_default="cobro")  # TRIGGERS + manual
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("quote.id", ondelete="SET NULL", name="fk_comm_quote"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id", ondelete="SET NULL", name="fk_comm_project"), index=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunity.id", ondelete="SET NULL", name="fk_comm_opportunity"))
    cost_request_id: Mapped[int | None] = mapped_column(ForeignKey("ally_cost_request.id", ondelete="SET NULL", name="fk_comm_cost_request"))
    source_key: Mapped[str | None] = mapped_column(String(120))  # idempotencia (uq_commission_source)
    fx: Mapped[float | None] = mapped_column(Numeric(12, 5))  # tipo de cambio usado para amount_crc
    amount_crc: Mapped[float | None] = mapped_column(Numeric(16, 2))  # equivalente en colones al devengar
    terms: Mapped[dict | None] = mapped_column(JSON)  # foto de la regla y de la base de venta al devengar
    description: Mapped[str | None] = mapped_column(String(240))  # concepto legible: NUNCA lleva montos de base
    hold_reason: Mapped[str | None] = mapped_column(String(240))  # por que esta en_espera
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_comm_approved_by"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_comm_paid_by"))
    paid_ref: Mapped[str | None] = mapped_column(String(120))  # comprobante: "Planilla oct-2026"
    expense_id: Mapped[int | None] = mapped_column(ForeignKey("expense.id", ondelete="SET NULL", name="fk_comm_expense"))
    void_reason: Mapped[str | None] = mapped_column(String(240))
    reverses_id: Mapped[int | None] = mapped_column(ForeignKey("commission.id", name="fk_comm_reverses"))
