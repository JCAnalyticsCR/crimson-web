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
from sqlalchemy import false as sa_false
from sqlalchemy import true as sa_true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base
from .base import TenantMixin, TimestampMixin

ALLY_ROLES = ("contratante", "referido", "subcontratista", "suministro", "configuracion", "software")
CONTACT_FUNCTIONS = ("comercial", "tecnico", "aprobador", "pagos")
# Solicitud de precio (revision 2, puntos 7-9): la PETICION de Crimson y la OFERTA del aliado son dos momentos.
# borrador (Crimson la arma, el aliado no la ve) -> enviado -> visto (el aliado la abrio) -> respondido (el aliado
# completo su oferta) -> aceptado (queda el acuerdo versionado, AllyAgreement) | rechazado. "vencido" no se guarda: es
# enviado/visto con la fecha limite pasada (services/allies.effective_status), asi no depende de una tarea programada.
COST_STATES = ("borrador", "enviado", "visto", "respondido", "aceptado", "rechazado")
COST_WAITING = ("enviado", "visto")  # espera la oferta del aliado
COST_OPEN = ("borrador", "enviado", "visto", "respondido")  # pendiente: falta enviarla, que llegue o decidirla
COST_CLOSED = ("aceptado", "rechazado")
# estados de antes de la revision 2 (la migracion los paso; la API los sigue aceptando como sinonimos)
COST_LEGACY = {"solicitado": "enviado", "recibido": "respondido", "aprobado": "aceptado"}
COST_SOURCES = ("crimson", "aliado")  # crimson: Crimson lo pidio | aliado: el aliado lo envio por iniciativa
# como respondera el aliado (punto 9): por su portal o por fuera (Crimson registra la oferta que llego)
RESPONSE_CHANNELS = ("portal", "correo", "whatsapp", "telefono")
PLAN_MODES = ("fija", "por_usuario")

# Alcances y responsables (revision 2, puntos 3 y A3): partidas por oportunidad (o por levantamiento sin oportunidad)
SCOPE_KINDS = ("mano_obra", "viaticos", "equipos", "materiales", "configuracion", "obra_civil", "otro")
SCOPE_STATES = ("por_confirmar", "confirmado", "en_curso", "completado")
# quien suministra / quien ejecuta: Crimson, una participacion de aliado de la oportunidad o nadie todavia
PARTY_KINDS = ("crimson", "aliado", "por_asignar")
AGREEMENT_STATES = ("vigente", "reemplazado", "anulado")


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
    # reunion 2 (21/10), pedido 14 y pregunta 8: el aliado NO ve el monto total de la cotizacion de una oportunidad en
    # comun salvo que Crimson lo habilite para esta empresa (Administracion -> Aliados -> ficha -> Que ve en su portal)
    show_quote_total: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false())
    plan_amount: Mapped[float | None] = mapped_column(Numeric(16, 2))  # mensualidad sin IVA (por empresa o por usuario)
    plan_currency: Mapped[str] = mapped_column(String(3), default="CRC", server_default="CRC")
    plan_mode: Mapped[str] = mapped_column(String(12), default="fija", server_default="fija")  # fija | por_usuario
    plan_since: Mapped[date | None] = mapped_column(Date)
    # revision 2, punto 13: el acceso basico al portal no se cobra (es parte del servicio compartido). La mensualidad
    # solo aplica a un SERVICIO ADICIONAL definido (extra_service) y aceptado (extra_service_accepted_at): sin
    # aceptacion la recurrencia queda apagada y la mora no propone suspender a nadie (ally_access.mora_where)
    extra_service: Mapped[str | None] = mapped_column(Text)
    extra_service_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extra_service_accepted_by: Mapped[str | None] = mapped_column(String(200))  # "Ana (portal)" o "registrado por X"
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
    # revision 2, A2: relaciones concretas en vez de un "Rol" general. is_origin = Aliado comercial (quien origino la
    # oportunidad); is_executor = Responsable de ejecucion (si ninguna lo es, ejecuta Crimson). Quien le paga a Crimson
    # es el customer_id de la oportunidad (la empresa aliada puede ser ese mismo cliente: AllyCompany.customer_id, sin
    # duplicar fichas). role se conserva por compatibilidad (comisiones: "contratante" no gana la regla general) y se
    # deriva de estas relaciones (routers/allies.py _sync_roles)
    is_origin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false())
    is_executor: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false())

    requests: Mapped[list["AllyCostRequest"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="AllyCostRequest.id", back_populates="participation"
    )


class AllyCostRequest(TenantMixin, TimestampMixin, Base):
    """Solicitud de precio a un aliado (la PETICION de Crimson) y, despues, su OFERTA.

    Peticion (la arma Crimson, sin precio ni vigencia): destinatario (la participacion), que se pide (what), alcance,
    partidas con cantidad y unidad (req_lines), sitio, fecha limite, inclusiones, exclusiones, adjuntos, el responsable
    interno de seguimiento (responsible_id) y como respondera el aliado (response_channel).
    Oferta (la completa el aliado desde su portal, o la registra quien ve costos si llego por fuera): monto o desglose
    (lines), moneda, vigencia, condiciones, sus exclusiones (exclusions) y adjuntos (attachments).
    Vencida o sin respuesta cuenta como pendiente en la oportunidad y en el inicio."""

    __tablename__ = "ally_cost_request"

    id: Mapped[int] = mapped_column(primary_key=True)
    participation_id: Mapped[int] = mapped_column(ForeignKey("ally_participation.id", ondelete="CASCADE", name="fk_costreq_participation"), index=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunity.id", ondelete="CASCADE", name="fk_costreq_opportunity"), index=True)
    what: Mapped[str] = mapped_column(String(300))
    responsible_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_costreq_responsible"), index=True)
    due_date: Mapped[date | None] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(12), default="enviado", index=True)
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
    # ---- revision 2: la peticion (lo que Crimson pide; el aliado la ve tal cual, sin montos) ----
    scope: Mapped[str | None] = mapped_column(Text)  # alcance de lo que se pide
    site: Mapped[str | None] = mapped_column(String(300))
    inclusions: Mapped[str | None] = mapped_column(Text)
    req_exclusions: Mapped[str | None] = mapped_column(Text)  # lo que Crimson NO pide (las del aliado: exclusions)
    req_lines: Mapped[list | None] = mapped_column(JSON, default=list)  # [{concept, quantity, unit, scope_item_id}]
    req_attachments: Mapped[list | None] = mapped_column(JSON, default=list)  # planos, fotos: [{id, url, filename}]
    response_channel: Mapped[str] = mapped_column(String(12), default="portal", server_default="portal")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # la primera vez que el aliado la abrio
    seen_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_costreq_seen_by"))
    # ---- la oferta (ademas de amount, currency, valid_until, exclusions, lines, attachments) ----
    conditions: Mapped[str | None] = mapped_column(Text)  # forma de pago, plazos, garantia
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_costreq_decided_by"))
    decision_note: Mapped[str | None] = mapped_column(String(400))

    participation: Mapped[AllyParticipation] = relationship(back_populates="requests")


class AllyAgreement(TenantMixin, TimestampMixin, Base):
    """Acuerdo aceptado con un participante (revision 2, punto 11): la foto de la oferta que Crimson acepto.

    Una cadena de versiones por solicitud (request_id): aceptar crea la version 1; cada cambio posterior (precio,
    moneda, vigencia, condiciones, exclusiones) crea la siguiente con quien, cuando y que cambio (changes) y deja la
    anterior "reemplazado". Rechazar despues de aceptar la deja "anulado". Nunca se edita una version: se agrega otra.

    Transparencia (punto 10): shared = el monto acordado afecta el presupuesto comun y lo ven los demas aliados de la
    oportunidad (por defecto si). El desglose de la oferta original (lines) solo lo ven los demas si la oportunidad
    trabaja con costos abiertos (Opportunity.ally_open_costs). El participante ve siempre los suyos."""

    __tablename__ = "ally_agreement"
    __table_args__ = (UniqueConstraint("request_id", "version", name="uq_ally_agreement_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunity.id", ondelete="CASCADE", name="fk_agreement_opportunity"), index=True)
    participation_id: Mapped[int] = mapped_column(ForeignKey("ally_participation.id", ondelete="CASCADE", name="fk_agreement_participation"), index=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("ally_cost_request.id", ondelete="CASCADE", name="fk_agreement_request"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(12), default="vigente", index=True)
    what: Mapped[str] = mapped_column(String(300))
    scope: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[float | None] = mapped_column(Numeric(16, 2))
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    valid_until: Mapped[date | None] = mapped_column(Date)
    conditions: Mapped[str | None] = mapped_column(Text)
    exclusions: Mapped[str | None] = mapped_column(Text)
    lines: Mapped[list | None] = mapped_column(JSON, default=list)  # desglose de la oferta original
    shared: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true())
    changes: Mapped[list | None] = mapped_column(JSON, default=list)  # ["precio 100 -> 120", ...]
    note: Mapped[str | None] = mapped_column(String(400))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_agreement_created_by"))


class ScopeItem(TenantMixin, TimestampMixin, Base):
    """Partida de alcance (revision 2, puntos 3 y A3): que hace falta para la obra, quien lo suministra, quien lo
    ejecuta y en que estado va. UNA sola fuente: ligada a la oportunidad (la ven igual el levantamiento, la oportunidad
    y su proyecto). Un levantamiento sin oportunidad guarda las suyas con survey_id; al ligarlo a una oportunidad pasan
    a ella (services/scope_items.attach_survey).

    Flujo: Crimson propone la distribucion (por_confirmar), el aliado asignado la confirma desde su portal
    (confirmado) y presenta su costo (solicitud de precio), quien aprueba acepta el acuerdo; despues en_curso y
    completado son el avance. Sin montos: lo economico vive en las solicitudes y los acuerdos.
    shared: la ven todos los aliados de la oportunidad (punto 10, por defecto); apagada solo la ve su responsable."""

    __tablename__ = "scope_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunity.id", ondelete="CASCADE", name="fk_scope_item_opportunity"), index=True)
    survey_id: Mapped[int | None] = mapped_column(ForeignKey("survey.id", ondelete="SET NULL", name="fk_scope_item_survey"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="otro")
    description: Mapped[str] = mapped_column(String(400))
    quantity: Mapped[float | None] = mapped_column(Numeric(12, 2))
    unit: Mapped[str | None] = mapped_column(String(12))
    supplier_kind: Mapped[str] = mapped_column(String(12), default="por_asignar", server_default="por_asignar")
    supplier_participation_id: Mapped[int | None] = mapped_column(ForeignKey("ally_participation.id", ondelete="SET NULL", name="fk_scope_item_supplier"))
    executor_kind: Mapped[str] = mapped_column(String(12), default="por_asignar", server_default="por_asignar")
    executor_participation_id: Mapped[int | None] = mapped_column(ForeignKey("ally_participation.id", ondelete="SET NULL", name="fk_scope_item_executor"))
    status: Mapped[str] = mapped_column(String(16), default="por_confirmar", server_default="por_confirmar")
    shared: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true())
    notes: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_scope_item_confirmed_by"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_scope_item_created_by"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_scope_item_updated_by"))


class AllySharedNote(TenantMixin, TimestampMixin, Base):
    """Entrada de la bitacora de una oportunidad que Crimson comparte con el aliado (reunion 2, pedido 15).

    La bitacora sigue siendo texto (Opportunity.notes, una linea por entrada, la mas nueva arriba): aqui solo vive la
    huella (sha256 de la linea) de cada entrada marcada "Visible para el aliado". Por defecto nada se comparte; una
    linea que se borre o se edite deja de verse sola (su huella ya no coincide con ninguna). Lo ven los aliados con
    una participacion visible en la oportunidad."""

    __tablename__ = "ally_shared_note"
    __table_args__ = (UniqueConstraint("opportunity_id", "line_key", name="uq_ally_shared_note"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunity.id", ondelete="CASCADE", name="fk_ally_shared_note_opportunity"), index=True)
    line_key: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_ally_shared_note_created_by"))
