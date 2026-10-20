"""Inventario, contabilidad, recurrencias, factura electronica, correo saliente y webhooks."""

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base
from .base import TenantMixin, TimestampMixin


# ---------- Inventario (plan 5.4): el saldo se calcula desde el ledger, nunca se edita ----------
class Warehouse(TenantMixin, Base):
    __tablename__ = "warehouse"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(160))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # venta: lo que se vende y se instala (tienda, facturas, proyectos) | revision: equipos de clientes que Crimson
    # revisa antes de mandar a garantia | proveedor: equipos que estan en el taller del distribuidor. Lo que esta en
    # revision o en el proveedor nunca se vende ni cuenta como existencia disponible.
    kind: Mapped[str] = mapped_column(String(12), default="venta", server_default="venta")


class StockMovement(TenantMixin, Base):
    """kind: entrada | salida | ajuste | transferencia_out | transferencia_in | venta | devolucion."""

    __tablename__ = "stock_movement"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"), index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouse.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[float] = mapped_column(Numeric(14, 3))  # positivo entra, negativo sale
    unit_cost: Mapped[float | None] = mapped_column(Numeric(14, 5))
    reference: Mapped[str | None] = mapped_column(String(120))  # FEC-000012, OC-4, ajuste
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoice.id"))
    note: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    # existencias enriquecidas (AC06/AC25): todo opcional; sin estos datos el movimiento es el de siempre
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id", name="fk_stock_movement_project"), index=True)
    serial: Mapped[str | None] = mapped_column(String(80))
    # compra | venta | consumo_proyecto | prestamo | consignacion | asignacion_interna | devolucion | ajuste | traslado
    movement_type: Mapped[str | None] = mapped_column(String(24))
    voucher_media: Mapped[str | None] = mapped_column(String(400))  # comprobante adjunto (url de multimedia)
    transfer_price: Mapped[float | None] = mapped_column(Numeric(14, 5))
    owner: Mapped[str | None] = mapped_column(String(160))  # vacio = la empresa
    custodian: Mapped[str | None] = mapped_column(String(160))  # vacio = la empresa
    location: Mapped[str | None] = mapped_column(String(160))  # sub-ubicacion: estante, rack, vehiculo
    # salida por un pedido (tienda): evita descontar dos veces entre despacho y factura
    order_id: Mapped[int | None] = mapped_column(ForeignKey("order.id", name="fk_stock_movement_order"), index=True)
    # movimiento de una garantia (services/warranty.py concilia el caso contra estas filas)
    warranty_id: Mapped[int | None] = mapped_column(ForeignKey("warranty_case.id", name="fk_stock_movement_warranty"), index=True)


MOVEMENT_TYPES = (
    "compra",
    "venta",
    "consumo_proyecto",
    "prestamo",
    "consignacion",
    "asignacion_interna",
    "devolucion",
    "ajuste",
    "traslado",
    "garantia",
)


class StockUnit(TenantMixin, TimestampMixin, Base):
    """Unidad serializada (solo productos con serialized=True). El saldo sigue saliendo del ledger;
    esto dice DONDE esta cada serie y en que estado."""

    __tablename__ = "stock_unit"
    __table_args__ = (UniqueConstraint("tenant_id", "serial", name="uq_stock_unit_serial"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", name="fk_stock_unit_product"), index=True)
    serial: Mapped[str] = mapped_column(String(80))
    warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouse.id", name="fk_stock_unit_warehouse"))
    # disponible | reservado | solicitado | en_transito | entregado_proyecto | instalado | devuelto | averiado | garantia
    status: Mapped[str] = mapped_column(String(24), default="disponible", index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id", name="fk_stock_unit_project"), index=True)
    condition: Mapped[str] = mapped_column(String(24), default="nuevo")  # nuevo | usado | reacondicionado | danado
    location: Mapped[str | None] = mapped_column(String(160))
    owner: Mapped[str | None] = mapped_column(String(160))
    custodian: Mapped[str | None] = mapped_column(String(160))
    customer_asset_id: Mapped[int | None] = mapped_column(ForeignKey("customer_asset.id", name="fk_stock_unit_asset"))
    notes: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_stock_unit_created_by"))


UNIT_STATUSES = (
    "disponible",
    "reservado",
    "solicitado",
    "en_transito",
    "entregado_proyecto",
    "instalado",
    "devuelto",
    "averiado",
    "garantia",
)


class StockReservation(TenantMixin, TimestampMixin, Base):
    """Reserva formal (AC25 "ver existencia no es retirar"). Solo las confirmadas y vigentes comprometen
    existencia; confirmar valida disponibilidad bajo candado (AC06)."""

    __tablename__ = "stock_reservation"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", name="fk_stock_reservation_product"), index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouse.id", name="fk_stock_reservation_warehouse"), index=True)
    quantity: Mapped[float] = mapped_column(Numeric(14, 3))
    consumed_quantity: Mapped[float] = mapped_column(Numeric(14, 3), default=0)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id", name="fk_stock_reservation_project"), index=True)
    survey_id: Mapped[int | None] = mapped_column(ForeignKey("survey.id", name="fk_stock_reservation_survey"))
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("quote.id", name="fk_stock_reservation_quote"))
    # pendiente | confirmada | cancelada | vencida | consumida
    status: Mapped[str] = mapped_column(String(16), default="pendiente", index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_stock_reservation_requested_by"))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_stock_reservation_approved_by"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)  # motivo de la reserva
    closed_note: Mapped[str | None] = mapped_column(String(300))  # por que se cancelo / vencio / consumio
    # interna (bodega/proyectos, requiere aprobacion) | web (pedido de la tienda, automatica)
    origin: Mapped[str] = mapped_column(String(12), default="interna", server_default="interna")
    order_id: Mapped[int | None] = mapped_column(ForeignKey("order.id", name="fk_stock_reservation_order"), index=True)


class StockLock(TenantMixin, Base):
    """Fila de control por (empresa, producto, bodega): confirmar una reserva la bloquea (SELECT ... FOR
    UPDATE en Postgres) para que dos confirmaciones simultaneas no se lleven la misma ultima unidad."""

    __tablename__ = "stock_lock"
    __table_args__ = (UniqueConstraint("tenant_id", "product_id", "warehouse_id", name="uq_stock_lock"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", name="fk_stock_lock_product"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouse.id", name="fk_stock_lock_warehouse"))
    version: Mapped[int] = mapped_column(Integer, default=0)


# ---------- Contabilidad (plan 3.7) ----------
class ExpenseCategory(TenantMixin, Base):
    __tablename__ = "expense_category"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # gasto especifico del D-151/D-270: alquiler | servicios_profesionales | comisiones | intereses (None = ninguno)
    d151_kind: Mapped[str | None] = mapped_column(String(24))


class Expense(TenantMixin, TimestampMixin, Base):
    __tablename__ = "expense"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("expense_category.id"))
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("supplier.id"))
    description: Mapped[str] = mapped_column(String(300))
    date: Mapped[date] = mapped_column(Date, index=True)
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    subtotal: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    tax_rate: Mapped[float] = mapped_column(Numeric(6, 3), default=13)
    tax_amount: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    total: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    # credito | credito_parcial | bienes_capital | no_credito | proporcional
    iva_credit: Mapped[str] = mapped_column(String(16), default="credito")
    bank_account_id: Mapped[int | None] = mapped_column(ForeignKey("bank_account.id"))
    reference: Mapped[str | None] = mapped_column(String(120))
    received_clave: Mapped[str | None] = mapped_column(String(50))  # clave del XML recibido
    attachment_url: Mapped[str | None] = mapped_column(String(400))
    status: Mapped[str] = mapped_column(String(20), default="registrado")  # registrado | pagado | anulado
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    # gasto que nace (o se vincula) de un comprobante electronico: los montos salen del XML y quedan bloqueados
    received_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("received_document.id", use_alter=True, name="fk_expense_received_document"), index=True
    )
    fx_rate: Mapped[float] = mapped_column(Numeric(12, 5), default=1)
    tax_credit_amount: Mapped[float | None] = mapped_column(Numeric(16, 5))  # IVA acreditable en la divisa del gasto
    notes: Mapped[str | None] = mapped_column(Text)
    payment_method: Mapped[str | None] = mapped_column(String(20))  # efectivo | tarjeta | transferencia | sinpe | cheque | otro
    purchase_type: Mapped[str] = mapped_column(String(10), default="contado")  # contado | credito
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # nullable explicito: el campo "date" tapa el tipo
    recurrence_id: Mapped[int | None] = mapped_column(ForeignKey("recurrence.id", use_alter=True, name="fk_expense_recurrence"))
    recurrence_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    bank_instrument_id: Mapped[int | None] = mapped_column(ForeignKey("bank_instrument.id", name="fk_expense_bank_instrument"))
    check_number: Mapped[str | None] = mapped_column(String(20))

    __table_args__ = (UniqueConstraint("tenant_id", "recurrence_id", "recurrence_date", name="uq_expense_recurrence_date"),)


# ---------- Recurrencias (plan 4): plantilla -> factura en la fecha programada ----------
class Recurrence(TenantMixin, TimestampMixin, Base):
    __tablename__ = "recurrence"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(10), default="factura", server_default="factura")  # factura | gasto
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.id"))  # solo facturas
    template: Mapped[dict] = mapped_column(JSON)  # DocumentIn (factura) o ExpenseIn (gasto) serializado
    frequency: Mapped[str] = mapped_column(String(12), default="mensual")  # semanal | quincenal | mensual | anual
    next_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date | None] = mapped_column(Date)
    auto_send: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_charge: Mapped[bool] = mapped_column(Boolean, default=False)  # con tarjeta tokenizada (Fase 3)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoice.id"))
    last_expense_id: Mapped[int | None] = mapped_column(ForeignKey("expense.id"))
    runs: Mapped[int] = mapped_column(Integer, default=0)
    anchor_day: Mapped[int | None] = mapped_column(SmallInteger)  # dia ancla: un 31 no "baja al 28" para siempre


# ---------- Factura electronica (plan 5.1) ----------
class EInvoiceDocument(TenantMixin, TimestampMixin, Base):
    """Un registro por comprobante enviado a Hacienda (FE/TE/FEE/NC/ND). XML y respuesta se guardan integros (5 anos)."""

    __tablename__ = "einvoice_document"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoice.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(4))
    consecutive: Mapped[str] = mapped_column(String(20))
    clave: Mapped[str | None] = mapped_column(String(50), index=True)
    reference_clave: Mapped[str | None] = mapped_column(String(50))  # NC/ND: clave del documento original
    provider: Mapped[str] = mapped_column(String(20))
    provider_ref: Mapped[str | None] = mapped_column(String(120))
    # por_enviar | enviando | enviado | procesando | pendiente | aceptada | rechazada | error
    status: Mapped[str] = mapped_column(String(20), default="pendiente")
    hacienda_message: Mapped[str | None] = mapped_column(Text)
    xml_document: Mapped[str | None] = mapped_column(Text)
    xml_response: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mode: Mapped[str | None] = mapped_column(String(12))  # simulado | pruebas | produccion
    emitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(300))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # XML + PDF entregados al cliente
    hacienda_delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # respuesta de Hacienda entregada
    # tope de reintentos de la entrega (correo rechazado una y otra vez): no se insiste y se avisa al admin
    delivery_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hacienda_delivery_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # mensaje receptor de NUESTRO cliente sobre este comprobante (1 acepta, 2 parcial, 3 rechaza)
    receiver_message: Mapped[str | None] = mapped_column(String(1))
    receiver_detail: Mapped[str | None] = mapped_column(String(160))
    receiver_xml: Mapped[str | None] = mapped_column(Text)
    receiver_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------- Correo saliente (outbox) y webhooks (plan 6, 5.3) ----------
class EmailOutbox(TenantMixin, TimestampMixin, Base):
    __tablename__ = "email_outbox"
    # dedupe_key: el mismo aviso automatico no sale dos veces (dos corridas, dos beats, un reintento de la tarea).
    # Unica por empresa; NULL (correos manuales) no choca con nada.
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key", name="uq_email_outbox_dedupe"),
        Index("ix_email_outbox_retry", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    to: Mapped[str] = mapped_column(String(300))
    bcc: Mapped[str | None] = mapped_column(String(600))
    subject: Mapped[str] = mapped_column(String(300))
    html: Mapped[str] = mapped_column(Text)
    attachments: Mapped[list] = mapped_column(JSON, default=list)  # [{name, kind, ref}]
    entity: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    # pendiente | enviado | error (se reintenta) | fallido (ya no se reintenta) | simulado (sin llave de correo)
    status: Mapped[str] = mapped_column(String(20), default="pendiente")
    provider_id: Mapped[str | None] = mapped_column(String(120))
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # envios intentados
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # cuando lo toma mail.retry_outbox


class TaskRun(Base):
    """Corrida de una tarea del worker (plan 1.8): cuando empezo y termino, si salio bien, un resumen y el error.
    La llenan las señales de Celery (app/tasks/salud.py); el latido del worker (salud.latido) deja aqui su fila y
    /health/deep la mira. De la plataforma, no de una empresa (las tareas recorren todas). Se purga a los 30 dias."""

    __tablename__ = "task_run"
    __table_args__ = (Index("ix_task_run_task_started", "task", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task: Mapped[str] = mapped_column(String(80))  # nombre de la tarea (sales.daily_close_email)
    task_id: Mapped[str | None] = mapped_column(String(64), index=True)  # id de Celery
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ok: Mapped[bool | None] = mapped_column(Boolean)  # None = corriendo (o el proceso murio a media tarea)
    summary: Mapped[dict | None] = mapped_column(JSON)  # lo que devolvio la tarea (conteos), recortado
    error: Mapped[str | None] = mapped_column(Text)  # tipo y mensaje, sin URLs con parametros ni llaves


class WebhookEvent(Base):
    """Evento crudo de proveedor (ONVO, PayPal, fiscal). Idempotente por (provider, external_id)."""

    __tablename__ = "webhook_event"
    __table_args__ = (UniqueConstraint("provider", "external_id", name="uq_webhook_provider_event"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), index=True)
    external_id: Mapped[str] = mapped_column(String(160))
    event_type: Mapped[str] = mapped_column(String(80))
    signature_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str | None] = mapped_column(Text)


class PaymentGatewayConfig(TenantMixin, Base):
    """Pasarelas por empresa (plan 3.8). El secreto se guarda cifrado con la clave del servidor."""

    __tablename__ = "payment_gateway_config"
    __table_args__ = (UniqueConstraint("tenant_id", "provider", name="uq_gateway_tenant_provider"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))  # onvo | paypal
    client_id: Mapped[str | None] = mapped_column(String(200))
    secret_encrypted: Mapped[str | None] = mapped_column(Text)  # ONVO: llave secreta de API (onvo_*_secret_key_)
    webhook_secret_encrypted: Mapped[str | None] = mapped_column(Text)  # ONVO: X-Webhook-Secret (otro valor que la llave)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    mode: Mapped[str] = mapped_column(String(10), default="test")  # test | live

    tenant = relationship("Tenant")
