"""Operacion de campo (sesion 5): la otra mitad del negocio de Crimson.

Oportunidad -> Levantamiento tecnico -> Cotizacion -> Proyecto -> Orden de trabajo -> Entrega -> Activos.
El tecnico nunca ve costos ni margenes: eso vive en el levantamiento del lado de administracion.
"""

from datetime import UTC, date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base
from .base import ArchiveMixin, TenantMixin, TimestampMixin, utcnow

# Tipos de levantamiento: cada uno pide datos distintos (ver services/survey_specs.py)
SURVEY_KINDS = ("cctv", "redes", "acceso", "asistencia", "ups", "cableado", "anpr", "otro")


class Opportunity(ArchiveMixin, TenantMixin, TimestampMixin, Base):
    """Antes de que exista una cotizacion: el prospecto y su seguimiento."""

    __tablename__ = "opportunity"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    # contratante: quien contrata y paga a Crimson; es a quien se cotiza y factura (no cambia de significado)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.id"))
    # cliente final: quien recibe la solucion cuando no es el contratante (Nodo Latam contrata, Yobel recibe)
    end_customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.id", name="fk_opportunity_end_customer"), index=True)
    site: Mapped[str | None] = mapped_column(String(300))  # sitio o direccion de la obra
    contact: Mapped[dict] = mapped_column(JSON, default=dict)  # nombre, correo, telefono (prospecto sin ficha aun)
    title: Mapped[str] = mapped_column(String(200))
    # venta: solo equipo, sin instalacion (no pide levantamiento ni proyecto) | proyecto: requiere levantamiento e instalacion
    kind: Mapped[str] = mapped_column(String(12), default="proyecto", server_default="proyecto")
    source: Mapped[str | None] = mapped_column(String(60))  # referido, web, whatsapp, feria, cliente actual
    solution: Mapped[str | None] = mapped_column(String(20))  # cctv, redes, acceso...
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), index=True)  # vendedora asignada
    amount: Mapped[float] = mapped_column(Numeric(16, 2), default=0)  # monto aproximado
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    probability: Mapped[int] = mapped_column(Integer, default=30)
    status: Mapped[str] = mapped_column(String(24), default="nuevo", index=True)
    # nuevo | contactado | requiere_visita | levantamiento | cotizando | enviada | negociacion | ganada | perdida
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # cuando paso a ganada o perdida
    next_action: Mapped[str | None] = mapped_column(String(200))
    next_action_date: Mapped[date | None] = mapped_column(Date, index=True)
    lost_reason: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("quote.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id", use_alter=True, name="fk_opportunity_project"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))


@event.listens_for(Opportunity.status, "set", active_history=True)
def _opportunity_closed_at(target, value, oldvalue, _initiator):
    """Fecha de cierre ("Ganado este mes"): la pone cualquier camino que cambie el estado (editar, seguimiento,
    convertir una venta, crear el proyecto). Reabrir la borra."""
    cerradas = ("ganada", "perdida")
    if value in cerradas and oldvalue not in cerradas:
        target.closed_at = datetime.now(UTC)
    elif value not in cerradas:
        target.closed_at = None


class Survey(ArchiveMixin, TenantMixin, TimestampMixin, Base):
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
    # techs/days quedan por compatibilidad: siempre reflejan el "personal tecnico" de labor.
    # Andres no sabia si "tecnicos" eran los que visitaron o los que hacen la obra; ahora son dos cosas:
    # visit_tech_ids (informativo, quien fue al sitio) y labor (personal requerido por tipo, para costear).
    techs: Mapped[int] = mapped_column(Integer, default=2)  # personal tecnico requerido para la obra
    days: Mapped[float] = mapped_column(Numeric(6, 2), default=1)  # dias del personal tecnico
    visit_tech_ids: Mapped[list | None] = mapped_column(JSON, default=list)  # usuarios que hicieron la visita
    labor: Mapped[dict | None] = mapped_column(JSON, default=dict)  # {tecnico|civil|contratado: {people, days}}
    sent_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))  # quien lo envio a revisión
    notes: Mapped[str | None] = mapped_column(Text)
    photos: Mapped[list] = mapped_column(JSON, default=list)
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("quote.id"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # revision del supervisor entre "enviado a revisión" y el costeo: None (sin enviar) | pendiente | aprobado | devuelto
    review_status: Mapped[str | None] = mapped_column(String(12), index=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_survey_reviewed_by"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_round: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # cuantas veces se reviso
    # seccion comun del §4 (objetivo, areas, energia, red...) y bloques de sistema (acceso_sistema, cctv_sistema,
    # parametros). None = levantamiento de antes: todo se calcula como siempre
    common: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    received_by: Mapped[str | None] = mapped_column(String(160))  # quien recibio al tecnico en el sitio
    # version de la plantilla (survey_specs.TEMPLATE_VERSION) con que se lleno; los existentes quedan en 1
    template_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # levantamiento a cargo de una empresa aliada (lo ve en su portal); NULL = de Crimson
    ally_company_id: Mapped[int | None] = mapped_column(ForeignKey("ally_company.id", ondelete="RESTRICT", name="fk_survey_ally_company"), index=True)

    points: Mapped[list["SurveyPoint"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="SurveyPoint.id")
    items: Mapped[list["SurveyItem"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="SurveyItem.id")
    observations: Mapped[list["SurveyObservation"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="SurveyObservation.id")


class SurveyObservation(Base):
    """Observacion del supervisor al devolver un levantamiento. Por punto (point_code; los ids de punto cambian
    en cada guardado, el codigo no) o general (point_code None). El tecnico la marca resuelta antes de reenviar."""

    __tablename__ = "survey_observation"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id", ondelete="CASCADE", name="fk_survey_observation_survey"), index=True)
    review_round: Mapped[int] = mapped_column(Integer, default=1)
    point_code: Mapped[str | None] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_survey_observation_created_by"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_survey_observation_resolved_by"))
    resolution: Mapped[str | None] = mapped_column(String(400))


class SurveyPoint(Base):
    """Un punto del levantamiento: CAM-01, DAT-007... Los campos varian segun el tipo (data JSON)."""

    __tablename__ = "survey_point"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(20))
    # tipo del punto cuando no es el del levantamiento (una puerta de acceso en uno de CCTV); None = el del levantamiento
    kind: Mapped[str | None] = mapped_column(String(16))
    label: Mapped[str | None] = mapped_column(String(160))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    # estado de cada campo, paralelo a data: {campo: confirmado|estimado|no_aplica|pendiente}. None = como siempre
    data_status: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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
    # equipo (camara, UPS, antena, gabinete) o material (cable, tubo, placa): el costeo los agrupa aparte
    kind: Mapped[str] = mapped_column(String(10), default="material", server_default="material")
    # costo unitario en colones escrito en el costeo cuando el producto no tiene costo de proveedor
    unit_cost: Mapped[float | None] = mapped_column(Numeric(14, 4))
    # recomendacion de equipos: oferta de proveedor elegida cuando no hay producto propio. Costo en su moneda;
    # el costeo lo pasa a colones con el tipo de cambio del dia. Sin FK: el catalogo de proveedor vive aparte.
    supplier_item_id: Mapped[int | None] = mapped_column(Integer)
    supplier_cost: Mapped[float | None] = mapped_column(Numeric(14, 4))
    supplier_currency: Mapped[str | None] = mapped_column(String(3))
    # que linea generica de la sugerencia cubre este equipo y como se eligio:
    # {line, line_unit, per_unit, source, supplier, code, model, brand, reason, chosen_by, chosen_at}
    recommendation: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Project(ArchiveMixin, TenantMixin, TimestampMixin, Base):
    """Cotizacion aprobada -> proyecto. Guarda lo cotizado y lo realmente gastado para saber el margen real."""

    __tablename__ = "project"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(200))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.id"), index=True)  # contratante
    end_customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.id", name="fk_project_end_customer"), index=True)  # cliente final
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
    price: Mapped[float] = mapped_column(Numeric(16, 2), default=0)  # venta sin IVA (subtotal - descuento de la cotizacion)
    cost_planned: Mapped[float] = mapped_column(Numeric(16, 2), default=0)  # costo estimado al cotizar
    cost_labor: Mapped[float] = mapped_column(Numeric(16, 2), default=0)  # costo real: mano de obra
    cost_travel: Mapped[float] = mapped_column(Numeric(16, 2), default=0)  # viaticos y transporte
    cost_extra: Mapped[float] = mapped_column(Numeric(16, 2), default=0)  # otros
    notes: Mapped[str | None] = mapped_column(Text)
    photos: Mapped[list] = mapped_column(JSON, default=list)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # la primera vez que entra a terminado | entregado | facturado; no se vuelve a tocar (devengos y bonos, economia)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    orders: Mapped[list["WorkOrder"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="WorkOrder.id")


class WorkOrder(ArchiveMixin, TenantMixin, TimestampMixin, Base):
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
    # orden asignada a una empresa aliada (sus tecnicos la ejecutan desde su portal); NULL = de Crimson
    ally_company_id: Mapped[int | None] = mapped_column(ForeignKey("ally_company.id", ondelete="RESTRICT", name="fk_work_order_ally_company"), index=True)

    materials: Mapped[list["WorkOrderMaterial"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="WorkOrderMaterial.id")
    time_entries: Mapped[list["WorkOrderTime"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="WorkOrderTime.started_at")


class WorkOrderTime(TenantMixin, TimestampMixin, Base):
    """Tiempo trabajado en una orden, por persona y por jornada. Las horas de la orden son la suma (persona-horas):
    una instalacion de dos dias no cuenta la noche y dos tecnicos cuentan doble."""

    __tablename__ = "work_order_time"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_order.id", ondelete="CASCADE", name="fk_work_order_time_order"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", name="fk_work_order_time_user"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # None = corriendo
    minutes: Mapped[int | None] = mapped_column(Integer)  # se fija al cerrar
    kind: Mapped[str] = mapped_column(String(10), default="reloj")  # reloj | manual
    note: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_work_order_time_created_by"))


class WorkOrderMaterial(Base):
    __tablename__ = "work_order_material"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey("work_order.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("product.id"))
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[float] = mapped_column(Numeric(14, 3), default=1)
    unit: Mapped[str] = mapped_column(String(10), default="Unid")
    planned: Mapped[float] = mapped_column(Numeric(14, 3), default=0)  # lo que decia la cotizacion


class CustomerAsset(ArchiveMixin, TenantMixin, TimestampMixin, Base):
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


class PurchaseRequest(ArchiveMixin, TenantMixin, TimestampMixin, Base):
    """Solicitud de compra: nace del stock bajo o de un proyecto. Los proveedores no dan credito: avisa temprano."""

    __tablename__ = "purchase_request"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("supplier.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id"))
    status: Mapped[str] = mapped_column(String(16), default="borrador", index=True)  # reservada (pedido web sin pagar) | borrador | solicitada | recibida | cancelada
    reason: Mapped[str] = mapped_column(String(200), default="stock_bajo")  # stock_bajo | proyecto | manual | pedido_web
    order_id: Mapped[int | None] = mapped_column(ForeignKey("order.id", name="fk_purchase_request_order"), index=True)  # pedido web bajo pedido
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
