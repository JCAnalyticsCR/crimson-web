"""Operacion de campo (sesion 5): la otra mitad del negocio de Crimson.

Oportunidad -> Levantamiento tecnico -> Cotizacion -> Proyecto -> Orden de trabajo -> Entrega -> Activos.
El tecnico nunca ve costos ni margenes: eso vive en el levantamiento del lado de administracion.
"""

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base
from .base import TenantMixin, TimestampMixin

# Tipos de levantamiento: cada uno pide datos distintos (ver services/survey_specs.py)
SURVEY_KINDS = ("cctv", "redes", "acceso", "asistencia", "ups", "cableado", "anpr", "otro")


class Opportunity(TenantMixin, TimestampMixin, Base):
    """Antes de que exista una cotizacion: el prospecto y su seguimiento."""

    __tablename__ = "opportunity"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.id"))
    contact: Mapped[dict] = mapped_column(JSON, default=dict)  # nombre, correo, telefono (prospecto sin ficha aun)
    title: Mapped[str] = mapped_column(String(200))
    source: Mapped[str | None] = mapped_column(String(60))  # referido, web, whatsapp, feria, cliente actual
    solution: Mapped[str | None] = mapped_column(String(20))  # cctv, redes, acceso...
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), index=True)  # vendedora asignada
    amount: Mapped[float] = mapped_column(Numeric(16, 2), default=0)  # monto aproximado
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    probability: Mapped[int] = mapped_column(Integer, default=30)
    status: Mapped[str] = mapped_column(String(24), default="nuevo", index=True)
    # nuevo | contactado | requiere_visita | levantamiento | cotizando | enviada | negociacion | ganada | perdida
    next_action: Mapped[str | None] = mapped_column(String(200))
    next_action_date: Mapped[date | None] = mapped_column(Date, index=True)
    lost_reason: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("quote.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id", use_alter=True, name="fk_opportunity_project"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))


class Survey(TenantMixin, TimestampMixin, Base):
    """Levantamiento tecnico hecho en sitio desde el celular. Alimenta la cotizacion sin volver a escribir nada."""

    __tablename__ = "survey"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="cctv")
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunity.id"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.id"), index=True)
    site: Mapped[str | None] = mapped_column(String(300))  # direccion / sede
    contact: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="borrador", index=True)  # borrador | enviado | cotizado | cerrado
    technician_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), index=True)
    visit_date: Mapped[date | None] = mapped_column(Date)
    techs: Mapped[int] = mapped_column(Integer, default=2)  # tecnicos necesarios
    days: Mapped[float] = mapped_column(Numeric(6, 2), default=1)  # dias estimados
    notes: Mapped[str | None] = mapped_column(Text)
    photos: Mapped[list] = mapped_column(JSON, default=list)
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("quote.id"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    points: Mapped[list["SurveyPoint"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="SurveyPoint.id")
    items: Mapped[list["SurveyItem"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="SurveyItem.id")


class SurveyPoint(Base):
    """Un punto del levantamiento: CAM-01, DAT-007... Los campos varian segun el tipo (data JSON)."""

    __tablename__ = "survey_point"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(20))
    label: Mapped[str | None] = mapped_column(String(160))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    photos: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text)


class SurveyItem(Base):
    """Material o equipo que el tecnico anota. El tecnico ve producto y cantidad; el costo lo pone el sistema."""

    __tablename__ = "survey_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("product.id"))
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[float] = mapped_column(Numeric(14, 3), default=1)
    unit: Mapped[str] = mapped_column(String(10), default="Unid")
    note: Mapped[str | None] = mapped_column(String(200))


class Project(TenantMixin, TimestampMixin, Base):
    """Cotizacion aprobada -> proyecto. Guarda lo cotizado y lo realmente gastado para saber el margen real."""

    __tablename__ = "project"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(200))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.id"), index=True)
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("quote.id"))
    survey_id: Mapped[int | None] = mapped_column(ForeignKey("survey.id"))
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunity.id"))
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoice.id"))
    site: Mapped[str | None] = mapped_column(String(300))
    scope: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="planificado", index=True)
    # planificado | en_curso | pausado | terminado | entregado | facturado | cancelado
    supervisor_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    price: Mapped[float] = mapped_column(Numeric(16, 2), default=0)  # venta (de la cotizacion)
    cost_planned: Mapped[float] = mapped_column(Numeric(16, 2), default=0)  # costo estimado al cotizar
    cost_labor: Mapped[float] = mapped_column(Numeric(16, 2), default=0)  # costo real: mano de obra
    cost_travel: Mapped[float] = mapped_column(Numeric(16, 2), default=0)  # viaticos y transporte
    cost_extra: Mapped[float] = mapped_column(Numeric(16, 2), default=0)  # otros
    notes: Mapped[str | None] = mapped_column(Text)
    photos: Mapped[list] = mapped_column(JSON, default=list)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    orders: Mapped[list["WorkOrder"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="WorkOrder.id")


class WorkOrder(TenantMixin, TimestampMixin, Base):
    """Trabajo de campo: instalacion, visita o mantenimiento. Es la pantalla del tecnico en el celular."""

    __tablename__ = "work_order"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16), default="instalacion")  # instalacion | visita | mantenimiento | soporte
    site: Mapped[str | None] = mapped_column(String(300))
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    technician_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), index=True)
    helpers: Mapped[list] = mapped_column(JSON, default=list)  # otros tecnicos (ids)
    status: Mapped[str] = mapped_column(String(16), default="asignada", index=True)
    # asignada | en_sitio | en_proceso | finalizada | cancelada
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tasks: Mapped[list] = mapped_column(JSON, default=list)  # [{text, done}]
    photos: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    customer_signature: Mapped[str | None] = mapped_column(String(160))  # nombre de quien recibe
    warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouse.id"))
    stock_applied: Mapped[bool] = mapped_column(Boolean, default=False)  # el consumo ya descontó inventario

    materials: Mapped[list["WorkOrderMaterial"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="WorkOrderMaterial.id")


class WorkOrderMaterial(Base):
    __tablename__ = "work_order_material"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_order.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("product.id"))
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[float] = mapped_column(Numeric(14, 3), default=1)
    unit: Mapped[str] = mapped_column(String(10), default="Unid")
    planned: Mapped[float] = mapped_column(Numeric(14, 3), default=0)  # lo que decia la cotizacion


class CustomerAsset(TenantMixin, TimestampMixin, Base):
    """Que quedo instalado donde: serie, IP, firmware, garantia. Historial tecnico del cliente."""

    __tablename__ = "customer_asset"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id"))
    work_order_id: Mapped[int | None] = mapped_column(ForeignKey("work_order.id"))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("product.id"))
    name: Mapped[str] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(120))
    serial: Mapped[str | None] = mapped_column(String(80), index=True)
    location: Mapped[str | None] = mapped_column(String(200))  # "Entrada principal", "Rack oficina"
    ip: Mapped[str | None] = mapped_column(String(45))
    mac: Mapped[str | None] = mapped_column(String(32))
    firmware: Mapped[str | None] = mapped_column(String(40))
    installed_at: Mapped[date | None] = mapped_column(Date)
    warranty_until: Mapped[date | None] = mapped_column(Date, index=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("supplier.id"))
    purchase_ref: Mapped[str | None] = mapped_column(String(120))  # factura de compra del equipo
    status: Mapped[str] = mapped_column(String(16), default="activo")  # activo | retirado | garantia | reemplazado
    notes: Mapped[str | None] = mapped_column(Text)
    photos: Mapped[list] = mapped_column(JSON, default=list)


class PurchaseRequest(TenantMixin, TimestampMixin, Base):
    """Solicitud de compra: nace del stock bajo o de un proyecto. Los proveedores no dan credito: avisa temprano."""

    __tablename__ = "purchase_request"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("supplier.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id"))
    status: Mapped[str] = mapped_column(String(16), default="borrador", index=True)  # borrador | solicitada | recibida | cancelada
    reason: Mapped[str] = mapped_column(String(200), default="stock_bajo")  # stock_bajo | proyecto | manual
    total_cost: Mapped[float] = mapped_column(Numeric(16, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))

    lines: Mapped[list["PurchaseRequestLine"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="PurchaseRequestLine.id")


class PurchaseRequestLine(Base):
    __tablename__ = "purchase_request_line"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("purchase_request.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("product.id"))
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[float] = mapped_column(Numeric(14, 3), default=1)
    unit_cost: Mapped[float] = mapped_column(Numeric(14, 4), default=0)
    note: Mapped[str | None] = mapped_column(String(200))
