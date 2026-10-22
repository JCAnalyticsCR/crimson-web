"""Mi Tienda (plan 3.3), ordenes, cupones, recepcion de XML de compras y credenciales de API publica."""

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base
from .base import ArchiveMixin, TenantMixin, TimestampMixin


class StorePage(TenantMixin, TimestampMixin, Base):
    """Pagina de la tienda: bloques JSON [{type, ...}] que renderiza el mismo componente en editor y sitio publico."""

    __tablename__ = "store_page"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_store_page_slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80))  # "inicio", "nosotros"...
    title: Mapped[str] = mapped_column(String(160))
    blocks: Mapped[list] = mapped_column(JSON, default=list)
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    in_nav: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)


class Coupon(ArchiveMixin, TenantMixin, Base):
    __tablename__ = "coupon"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_coupon_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(8), default="percent")  # percent | amount
    value: Mapped[float] = mapped_column(Numeric(14, 5))
    min_total: Mapped[float] = mapped_column(Numeric(14, 5), default=0)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    max_uses: Mapped[int | None] = mapped_column(Integer)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Order(TenantMixin, TimestampMixin, Base):
    """Pedido de tienda/POS/link previo a factura o tiquete (plan 3.9)."""

    __tablename__ = "order"
    # doble clic / reintento del navegador: la misma clave devuelve el mismo pedido
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key", name="uq_order_idempotency"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    channel: Mapped[str] = mapped_column(String(10), default="tienda")  # tienda | pos | link
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.id"))
    contact: Mapped[dict] = mapped_column(JSON, default=dict)  # nombre, correo, telefono, identificacion, direccion
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    subtotal: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    discount_total: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    shipping: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    tax_total: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    total: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    coupon_code: Mapped[str | None] = mapped_column(String(40))
    shipping_method: Mapped[str | None] = mapped_column(String(80))
    payment_method: Mapped[str | None] = mapped_column(String(40))
    # nuevo (pendiente de pago) | en_revision (comprobante) | pagado | preparando | listo | enviado | entregado | cancelado | reembolsado
    status: Mapped[str] = mapped_column(String(20), default="nuevo", index=True)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoice.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    # checkout de la tienda (tienda_ck)
    payment_kind: Mapped[str | None] = mapped_column(String(16))  # sinpe | transferencia | tarjeta | efectivo
    delivery: Mapped[dict | None] = mapped_column(JSON)  # tipo, provincia, canton, distrito, senas, zona
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_ref: Mapped[str | None] = mapped_column(String(120))  # intento ONVO, referencia SINPE
    tracking_number: Mapped[str | None] = mapped_column(String(80))
    carrier: Mapped[str | None] = mapped_column(String(80))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    refund: Mapped[dict | None] = mapped_column(JSON)  # registro del reembolso (no mueve dinero)
    history: Mapped[list | None] = mapped_column(JSON)  # [{status, at, by, note}]
    proofs: Mapped[list | None] = mapped_column(JSON)  # comprobantes subidos: [{media_id, filename, size, at}]
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # plazo para pagar
    inventory_state: Mapped[str | None] = mapped_column(String(12))  # held | released | fulfilled
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # vendedor del pedido web (21/10, items 24/42): lo asigna el admin o lo "toma" el vendedor. La factura que nace del
    # pedido queda a su nombre (seller_id) y comisiona como una venta propia salvo que Mi Tienda lo apague.
    seller_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), index=True)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # como se ligo a la ficha del cliente (21/10, item 43): "email" (la ficha tenia el correo del comprador o no tenia
    # correo) o "cedula" (solo coincidio la cedula; la ficha tenia OTRO correo). customer_link_email: el correo que
    # tenia la ficha al ligarla (interno, nunca sale al comprador). Con "cedula" los avisos siguen al correo del pedido
    # hasta que el equipo cambie el correo de la ficha (store_orders.notify_email).
    customer_link: Mapped[str | None] = mapped_column(String(8))
    customer_link_email: Mapped[str | None] = mapped_column(String(254))

    lines: Mapped[list["OrderLine"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class OrderLine(Base):
    __tablename__ = "order_line"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("product.id"))
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[float] = mapped_column(Numeric(14, 3), default=1)
    unit_price: Mapped[float] = mapped_column(Numeric(14, 5), default=0)
    discount_type: Mapped[str] = mapped_column(String(8), default="percent")
    discount_value: Mapped[float] = mapped_column(Numeric(14, 5), default=0)
    tax_rate: Mapped[float] = mapped_column(Numeric(6, 3), default=13)
    subtotal: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    tax_amount: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    total: Mapped[float] = mapped_column(Numeric(16, 5), default=0)


class ReceivedDocument(TenantMixin, TimestampMixin, Base):
    """Comprobante electronico recibido de un proveedor (XML): recepcion ante Hacienda + gasto (plan 3.4 / eje 3).
    Ver services/reception*.py. Nunca se borra (descartar = conservar 5 anos)."""

    __tablename__ = "received_document"
    __table_args__ = (UniqueConstraint("tenant_id", "clave", name="uq_received_document_clave"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    clave: Mapped[str] = mapped_column(String(50), index=True)
    consecutive: Mapped[str | None] = mapped_column(String(20))
    issuer_name: Mapped[str | None] = mapped_column(String(200))
    # tal cual viene en el XML (sin separadores, mayusculas): desde nov-2026 hay cedulas juridicas alfanumericas
    issuer_id: Mapped[str | None] = mapped_column(String(30))
    issue_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    subtotal: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    tax_total: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    total: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    # credito | credito_parcial | bienes_capital | no_credito | proporcional
    iva_condition: Mapped[str] = mapped_column(String(16), default="credito")
    activity_code: Mapped[str | None] = mapped_column(String(10))  # actividad de la EMPRESA elegida al responder
    action: Mapped[str | None] = mapped_column(String(12))  # aceptada | parcial | rechazada
    # copia del estado del ultimo mensaje: sin_respuesta | por_enviar | enviando | enviado | procesando | aceptado |
    # rechazado | error | simulado | historico. "simulado" nunca se muestra como aceptado.
    hacienda_status: Mapped[str] = mapped_column(String(20), default="sin_respuesta")
    xml_document: Mapped[str | None] = mapped_column(Text)
    xml_response: Mapped[str | None] = mapped_column(Text)  # mensaje heredado (antes de reception_response)
    expense_id: Mapped[int | None] = mapped_column(ForeignKey("expense.id"))
    doc_type: Mapped[str] = mapped_column(String(4), default="FE")  # FE | ND | NC | TE | FEC | FEE | REP
    schema_version: Mapped[str | None] = mapped_column(String(5))
    issuer_id_type: Mapped[str | None] = mapped_column(String(2))
    issuer_email: Mapped[str | None] = mapped_column(String(200))
    issuer_activity_code: Mapped[str | None] = mapped_column(String(10))  # la del proveedor
    receiver_id: Mapped[str | None] = mapped_column(String(20))
    receiver_ok: Mapped[bool | None] = mapped_column(Boolean)
    fx_rate: Mapped[float] = mapped_column(Numeric(12, 5), default=1)
    other_charges: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    iva_total: Mapped[float | None] = mapped_column(Numeric(16, 5))  # solo IVA (01, 07, 08): base del credito
    reference_clave: Mapped[str | None] = mapped_column(String(50))
    # por_revisar | respondido | sin_respuesta | ajeno | invalido | descartado | historico
    status: Mapped[str] = mapped_column(String(16), default="por_revisar", index=True)
    origin: Mapped[str] = mapped_column(String(10), default="manual")  # correo | manual | historico
    message_id: Mapped[int | None] = mapped_column(ForeignKey("reception_message.id", name="fk_received_document_message"))
    pdf_media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id", name="fk_received_document_pdf"))
    xml_supplier_response: Mapped[str | None] = mapped_column(Text)  # MensajeHacienda del proveedor
    supplier_hacienda_status: Mapped[str] = mapped_column(String(12), default="desconocido")  # aceptado | rechazado | desconocido
    supplier_hacienda_detail: Mapped[str | None] = mapped_column(String(500))
    credit_amount: Mapped[float | None] = mapped_column(Numeric(16, 5))  # IVA acreditable (divisa del documento)
    applicable_amount: Mapped[float | None] = mapped_column(Numeric(16, 5))  # gasto aplicable
    # aceptacion parcial: monto e IVA aceptados (con condicion 05 no hay credito ni gasto aplicable de donde sacarlos)
    accepted_total: Mapped[float | None] = mapped_column(Numeric(16, 5))
    accepted_tax: Mapped[float | None] = mapped_column(Numeric(16, 5))
    response_detail: Mapped[str | None] = mapped_column(String(160))
    deadline: Mapped[date | None] = mapped_column(Date)
    responded_by: Mapped[int | None] = mapped_column(Integer)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discarded_reason: Mapped[str | None] = mapped_column(String(200))
    discarded_by: Mapped[int | None] = mapped_column(Integer)
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_before_discard: Mapped[str | None] = mapped_column(String(16))


class ApiCredential(TenantMixin, TimestampMixin, Base):
    """API publica: public key (kid) + secret (solo hash). Checkout por JWT HS256 firmado con el secreto; webhooks HMAC."""

    __tablename__ = "api_credential"

    id: Mapped[int] = mapped_column(primary_key=True)
    kid: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    secret_hash: Mapped[str] = mapped_column(String(64))
    secret_encrypted: Mapped[str] = mapped_column(Text)  # para firmar webhooks salientes
    webhook_url: Mapped[str | None] = mapped_column(String(400))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StoreStockAlert(TenantMixin, Base):
    """"Avisarme cuando haya": el cliente deja correo o telefono en un producto agotado; el worker avisa al volver."""

    __tablename__ = "store_stock_alert"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", name="fk_store_stock_alert_product", ondelete="CASCADE"), index=True)
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
