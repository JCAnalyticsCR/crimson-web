"""Vista previa de los correos del sistema: un .html por tipo de correo, con datos de EJEMPLO, para revisarlos en el
navegador (y en el telefono, achicando la ventana) antes de publicar un cambio de diseno.

Uso:  python -m app.tools.preview_emails CARPETA

- Usa una base SQLite en memoria propia (nunca la de la plataforma) y quita RESEND_API_KEY del proceso: no sale ningun
  correo, todo queda "simulado" en esa base desechable.
- Los datos son inventados (Empresa Ejemplo, Cliente Ejemplo, correos @ejemplo.com): nada de clientes reales.
- Llama a las mismas funciones que arman los correos reales (doc_email_html, reminder_email, el correo del pedido web,
  invitaciones, avisos internos de tickets, campo, compras, margen, salud...) y, cuando la funcion encola, toma el
  HTML de la cola: lo que se ve es lo que sale.
- Al final escribe index.html con un enlace a cada uno.
"""

from __future__ import annotations

import html
import os
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

HOY = date(2026, 10, 21)


def _session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from .. import models  # noqa: F401 - registra todas las tablas
    from ..core.db import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed(db):
    from ..models import Customer, Tenant, TenantUser, User

    t = Tenant(
        slug="empresa-ejemplo",
        name="Empresa Ejemplo",
        legal_name="Empresa Ejemplo S.A.",
        settings={
            "phones": [{"number": "2222-0000", "kind": "whatsapp", "main": True}],
            "addresses": ["San José, Costa Rica"],
            "store": {"whatsapp": "8888-0000", "published": True},
        },
    )
    db.add(t)
    db.flush()
    admin = User(email="admin@ejemplo.com", full_name="Ana Administradora", password_hash="x")
    vend = User(email="vendedor@ejemplo.com", full_name="Víctor Vendedor", password_hash="x")
    tec = User(email="tecnico@ejemplo.com", full_name="Tomás Técnico", password_hash="x")
    db.add_all([admin, vend, tec])
    db.flush()
    db.add_all(
        [
            TenantUser(tenant_id=t.id, user_id=admin.id, role_code="admin", active=True),
            TenantUser(tenant_id=t.id, user_id=vend.id, role_code="ventas", active=True),
            TenantUser(tenant_id=t.id, user_id=tec.id, role_code="tecnico", active=True),
        ]
    )
    c = Customer(tenant_id=t.id, name="Cliente Ejemplo S.A.", email="cliente@ejemplo.com", id_type="juridica", id_number="3101000000")
    db.add(c)
    db.flush()
    # un usuario de una empresa aliada (trabajo asignado, solicitud de precios)
    from ..models import AllyCompany

    aliada = AllyCompany(tenant_id=t.id, name="Aliado Ejemplo S.A.", email="aliado@ejemplo.com")
    aliado = User(email="aliado@ejemplo.com", full_name="Ernesto Aliado", password_hash="x")
    db.add_all([aliada, aliado])
    db.flush()
    db.add(TenantUser(tenant_id=t.id, user_id=aliado.id, role_code="aliado", active=True, ally_company_id=aliada.id))
    # dos facturas por cobrar (una vencida) para el resumen semanal de cartera
    db.add_all([_invoice(t, c, id=601, number="FEC-000601", due_date=HOY - timedelta(days=40)), _invoice(t, c, id=602, number="FEC-000602")])
    db.flush()
    return t, admin, c


def _invoice(t, c, **kw):
    from ..models import Invoice

    base = {
        "id": 501,
        "tenant_id": t.id,
        "customer_id": c.id,
        "number": "FEC-000501",
        "currency": "CRC",
        "issue_date": HOY - timedelta(days=8),
        "due_date": HOY + timedelta(days=3),
        "total": Decimal("226000"),
        "balance": Decimal("226000"),
        "status": "enviada",
        "einvoice_status": "sin_emitir",
    }
    base.update(kw)
    return Invoice(**base)


def _order(t, status: str = "nuevo", **kw):
    from ..models import Order, OrderLine

    o = Order(
        id=77,
        tenant_id=t.id,
        number="TW-000077",
        contact={"name": "Carla Compradora", "email": "carla@ejemplo.com", "phone": "8800-0000"},
        currency="CRC",
        subtotal=Decimal("150000"),
        shipping=Decimal("3500"),
        tax_total=Decimal("19955"),
        total=Decimal("173455"),
        shipping_method="Envío GAM",
        payment_method="SINPE Móvil",
        payment_kind="sinpe",
        status=status,
        delivery={"type": "envio"},
        **kw,
    )
    o.lines = [
        OrderLine(name="Cámara IP 4 MP", quantity=Decimal("2"), unit_price=Decimal("45000"), total=Decimal("101700")),
        OrderLine(name="Grabador NVR 8 canales", quantity=Decimal("1"), unit_price=Decimal("60000"), total=Decimal("67800")),
    ]
    return o


def _outbox_html(db, before: int, to: str | None = None) -> str:
    from sqlalchemy import select

    from ..models import EmailOutbox

    q = select(EmailOutbox).where(EmailOutbox.id > before)
    if to:
        q = q.where(EmailOutbox.to == to)
    m = db.scalars(q.order_by(EmailOutbox.id)).first()
    if m is None:
        raise RuntimeError("no se encolo ningun correo")
    return m.html


def _last_id(db) -> int:
    from sqlalchemy import func, select

    from ..models import EmailOutbox

    return db.scalar(select(func.max(EmailOutbox.id))) or 0


def _queued(db, fn: Callable[[], object], to: str | None = None) -> str:
    """Corre una funcion que encola un correo y devuelve el HTML que quedo en la cola (lo que sale de verdad). Con `to`,
    el primero que fue a esa direccion (una funcion que avisa a varios: el resumen al equipo y el aviso al responsable)."""
    antes = _last_id(db)
    fn()
    db.flush()
    return _outbox_html(db, antes, to)


def samples(db, t, admin, c) -> dict[str, Callable[[], str]]:
    from sqlalchemy import select

    from ..models import User
    from ..services import accounts, avisos_venta, onvo
    from ..services import store_orders as so
    from ..services.einvoice_delivery import _html as einvoice_html
    from ..services.mail import doc_email_html, queue_email
    from ..services.store_inventory import back_in_stock_html
    from ..services.survey_report import report_email_html
    from ..tasks import cobranza
    from ..tasks.fiscal import digest_html
    from ..tools.respaldo_diario import Fallo, html_falla

    inv = _invoice(t, c)
    plan = _invoice(t, c, number="FEC-000502", external_order="MENSUALIDAD-PLATAFORMA", total=Decimal("45000"), balance=Decimal("45000"))
    link = "https://panel.ejemplo.com/pagar/TOKEN-DE-EJEMPLO"

    def recordatorio(dias: int, factura=inv) -> str:
        factura.due_date = HOY - timedelta(days=dias)
        return cobranza.reminder_email(t, factura, dias, HOY, link, attached=True)[1]

    def pedido(kind: str, status: str, **kw) -> str:
        o = _order(t, status, **kw)
        return so._email_html(t, o, kind, so.status_extra(t, o, kind, "El monto del comprobante no coincide con el total."))

    def interno_pedido() -> str:
        return _queued(db, lambda: so.notify_team(db, t, _order(t, "en_revision"), "comprobante"))

    def recibo_onvo() -> str:
        factura = _invoice(t, c, balance=Decimal("63000"))
        return _queued(db, lambda: onvo._receipt(db, t, factura, Decimal("163000"), "pi_EJEMPLO"))

    def alerta_onvo() -> str:
        return _queued(db, lambda: onvo._invoice_alert(db, t, inv, "Pago con tarjeta en otra moneda", "ONVO cobró en USD una factura en colones.", red=True))

    def seguridad() -> str:
        return _queued(
            db,
            lambda: accounts.notify_security(
                db, t, admin, "Su contraseña de Crimson cambió", "<p>Si no fue usted, avise de inmediato a la administración.</p>"
            ),
        )

    def por_emitir() -> str:
        fila = _invoice(t, c, customer_id=None, status="enviada")
        return digest_html(db, t, [fila], 1, HOY, 2, "ninguno")

    def cartera() -> str:
        return _queued(db, lambda: cobranza.weekly_summary(db, HOY))

    def recurrencias() -> str:
        from ..tasks.mensualidades import preview_html

        filas = [
            ("Monitoreo mensual", "Cliente Ejemplo S.A.", "₡45,000.00", "Sale sola con su PDF y enlace de pago"),
            ("Mantenimiento de cámaras", "Cliente Ejemplo S.A.", "₡120,000.00", "Espera su «Aprobar y enviar» en Aprobaciones"),
        ]
        asunto = "En 3 días (24/10/2026): 2 cobro(s) recurrente(s) · Empresa Ejemplo"
        return _queued(db, lambda: queue_email(db, t, "admin@ejemplo.com", asunto, preview_html(HOY + timedelta(days=3), filas), "recurrence", 0))

    def no_llegaron() -> str:
        from ..models import EmailOutbox
        from ..tasks.correo import _notify_owners

        fallido = EmailOutbox(
            id=9001,
            tenant_id=t.id,
            to="cliente@ejemplo.com",
            subject="Factura FEC-000501 · Empresa Ejemplo",
            entity="invoice",
            entity_id=999,
            error="550 buzón inexistente",
        )
        return _queued(db, lambda: _notify_owners(db, [fallido]))

    def entrega_fallida() -> str:
        from ..models import EInvoiceDocument
        from ..services.einvoice_delivery import _alert_undeliverable

        doc = EInvoiceDocument(tenant_id=t.id, invoice_id=inv.id, doc_type="FE", mode="produccion")
        return _queued(db, lambda: _alert_undeliverable(db, t, doc, inv, "emitido", ["cliente@ejemplo.com"]))

    def hacienda_diaria() -> str:
        from ..services.hacienda_tasks import daily_alert_html

        items = ["2 comprobante(s) recibido(s) sin aceptar: el plazo vence en 3 días hábiles", "1 factura emitida rechazada por Hacienda: FEC-000501"]
        return _queued(
            db, lambda: queue_email(db, t, "admin@ejemplo.com", "Hacienda: 2 pendiente(s) · Empresa Ejemplo", daily_alert_html(items), "hacienda", 0)
        )

    def onvo_webhook(status: int) -> Callable[[], str]:
        detalle = "X-Webhook-Secret no coincide" if status == 401 else "ONVO no respondió al consultar la sesión cs_EJEMPLO"
        return lambda: onvo.webhook_alert_html(t, status, detalle)[1]

    def encolado(fn: Callable[[], object]) -> Callable[[], str]:
        return lambda: _queued(db, fn)

    def cotizacion_aceptada() -> object:
        from ..models import Quote

        q = Quote(id=188, tenant_id=t.id, customer_id=c.id, number="COT-000188", status="convertida", seller_id=admin.id, created_by=admin.id)
        return avisos_venta.cotizacion_aceptada(db, t, q, version=2, quien="Carla Cliente", medio="correo", actor_id=None)

    def ot_asignada() -> object:
        from ..models import WorkOrder

        o = WorkOrder(
            id=31,
            tenant_id=t.id,
            customer_id=c.id,
            number="OT-000031",
            title="Instalación de 4 cámaras",
            site="Bodega central",
            scheduled_at=datetime(2026, 10, 23, 15, tzinfo=UTC),
        )
        return avisos_venta.ot_asignada(db, t, o, [admin.id], None)

    def trabajo_aliado() -> object:
        from ..services.ally_scope import notify_ally_work

        aliado = db.scalar(select(User).where(User.email == "aliado@ejemplo.com"))
        return notify_ally_work(db, t, [aliado.id], "OT-000031", "Instalación de 4 cámaras", HOY + timedelta(days=2), 31)

    def precios_aliado() -> object:
        from types import SimpleNamespace

        from ..models import AllyCompany
        from ..routers.allies import _notify_ally_request

        aliada = db.scalar(select(AllyCompany).where(AllyCompany.tenant_id == t.id))
        o = SimpleNamespace(id=12, title="Videovigilancia para bodega")
        r = SimpleNamespace(id=5, what="Precio de instalación de 4 cámaras con cableado", due_date=HOY + timedelta(days=4))
        return _notify_ally_request(db, SimpleNamespace(tenant=t), SimpleNamespace(ally_company_id=aliada.id), o, r)

    def entradas() -> object:
        from ..models import Event, Ticket
        from ..routers.events import _email_tickets

        ev = Event(id=3, tenant_id=t.id, name="Charla de seguridad electrónica", starts_at=datetime(2026, 11, 5, 23, tzinfo=UTC), venue="San José")
        boleto = Ticket(id=41, tenant_id=t.id, event_id=3, code="ABCDE-12345", holder_name="Carla Compradora", holder_email="carla@ejemplo.com")
        return _email_tickets(db, t, ev, [boleto])

    return {
        "cotizacion": lambda: doc_email_html(
            t, "cotización", "COT-000188", "₡226,000.00", None, "Gracias por su interés. Quedamos atentos.", issued="21/10/2026", due="05/11/2026"
        ),
        "cotizacion-con-portal": lambda: doc_email_html(
            t, "cotización", "COT-000188", "₡226,000.00", None, None, issued="21/10/2026", due="05/11/2026", view_link="https://panel.ejemplo.com/documentos"
        ),
        "factura-con-pago": lambda: doc_email_html(t, "factura", inv.number, "₡226,000.00", link, None, issued="13/10/2026", due="24/10/2026"),
        "aviso-de-cobro-mensualidad": lambda: doc_email_html(t, "aviso de cobro", plan.number, "₡45,000.00", link, None, issued="01/10/2026", due="10/10/2026"),
        "cobro-recordatorio-antes": lambda: recordatorio(-3),
        "cobro-vence-hoy": lambda: recordatorio(0),
        "cobro-atraso": lambda: recordatorio(10),
        "cobro-mensualidad-atraso": lambda: recordatorio(5, plan),
        "pedido-recibido": lambda: pedido("recibido", "nuevo"),
        "pedido-pagado": lambda: pedido("pagado", "pagado", paid_at=datetime.now(UTC)),
        "pedido-rechazado": lambda: pedido("rechazado", "nuevo"),
        "pedido-enviado": lambda: pedido("enviado", "enviado", paid_at=datetime.now(UTC)),
        "pedido-vencido": lambda: pedido("vencido", "cancelado"),
        "pedido-por-vencer": lambda: pedido("por_vencer", "nuevo"),
        "pedido-preparando": lambda: pedido("preparando", "preparando", paid_at=datetime.now(UTC)),
        "pedido-listo-para-retiro": lambda: pedido("listo", "listo", paid_at=datetime.now(UTC)),
        "pedido-entregado": lambda: pedido("entregado", "entregado", paid_at=datetime.now(UTC)),
        "pedido-cancelado": lambda: pedido("cancelado", "cancelado", cancel_reason="El cliente pidió cancelar antes del pago."),
        "pedido-reembolsado": lambda: pedido("reembolsado", "reembolsado", paid_at=datetime.now(UTC), refund={"amount": "173455"}),
        "ya-hay-existencia": lambda: back_in_stock_html(t, "Cámara IP 4 MP", "Disponible", "https://tienda.ejemplo.com/producto/camara-ip-4mp"),
        "entradas-evento": encolado(entradas),
        "pedido-aviso-interno": interno_pedido,
        "invitacion-panel": lambda: accounts.invite_email_html(t, "ventas", "https://panel.ejemplo.com/invitacion/TOKEN-DE-EJEMPLO", 7),
        "restablecer-contrasena": lambda: accounts.reset_email_html(
            t, "Ana Administradora", "https://panel.ejemplo.com/restablecer/TOKEN-DE-EJEMPLO", "24 horas"
        ),
        "aviso-de-seguridad": seguridad,
        "acceso-portal-aliados": lambda: accounts.access_email_html(
            t,
            title="Su acceso al portal de aliados",
            name="Ernesto Aliado",
            intro_html="Empresa Ejemplo le dio acceso al portal de aliados como Administrador de <b>Aliado Ejemplo S.A.</b>.",
            link="https://panel.ejemplo.com/invitacion/TOKEN-DE-EJEMPLO",
            label="Entrar al portal",
            note="Con el botón crea su contraseña y entra. El enlace vence en 7 días y sirve una sola vez.",
        ),
        "acceso-portal-clientes": lambda: accounts.access_email_html(
            t,
            title="Su acceso al portal de clientes",
            name="Carla Cliente",
            intro_html="Empresa Ejemplo le dio acceso al portal de clientes de <b>Cliente Ejemplo S.A.</b> (Usuario).",
            link="https://panel.ejemplo.com/invitacion/TOKEN-DE-EJEMPLO",
            label="Crear mi contraseña",
            note="El enlace vence en 7 días y sirve una sola vez. Si no esperaba este acceso, ignore este correo.",
        ),
        "comprobante-electronico": lambda: einvoice_html(
            t,
            f"Factura electrónica {inv.number}",
            [
                f"Le compartimos la <b>factura electrónica {inv.number}</b> (clave 506000000000000000000000000000000000000000000000).",
                "Adjuntamos el XML y el PDF.",
            ],
            True,
        ),
        "recibo-pago-en-linea": recibo_onvo,
        "alerta-pago-onvo": alerta_onvo,
        "cola-por-emitir": por_emitir,
        "cartera-semanal": cartera,
        "cobro-recordatorios-sin-salir": lambda: cobranza.failed_reminders_html(t, ["FEC-000501", "FEC-000502"])[1],
        "recurrencias-proximas": recurrencias,
        "correos-que-no-llegaron": no_llegaron,
        "comprobante-entrega-fallida": entrega_fallida,
        "hacienda-alerta-diaria": hacienda_diaria,
        "onvo-webhook-secreto": onvo_webhook(401),
        "onvo-webhook-sin-confirmar": onvo_webhook(503),
        "venta-cotizacion-aceptada": encolado(cotizacion_aceptada),
        "venta-orden-asignada": encolado(ot_asignada),
        "aliado-trabajo-asignado": encolado(trabajo_aliado),
        "aliado-solicitud-de-precios": encolado(precios_aliado),
        "informe-preliminar": lambda: report_email_html(
            t, "LEV-000014", "Cliente Ejemplo S.A.", "Bodega central", "Adjuntamos el informe del recorrido. Quedamos atentos.", attached_pdf=True
        ),
        "respaldo-diario-falla": lambda: html_falla([Fallo(5, "subida", "rclone: el destino no respondió")], {"subido": False}, HOY),
        **avisos_internos(db, t, admin, c, inv),
    }


def avisos_internos(db, t, admin, c, inv) -> dict[str, Callable[[], str]]:
    """Avisos al equipo (tickets, campo, compras, margen, salud, recurrencias, Hacienda, accesos, tienda...). Cada uno
    llama a la funcion que arma ese correo en la plataforma, con datos de ejemplo sembrados en la base en memoria."""
    from types import SimpleNamespace

    from sqlalchemy import select

    from ..models import (
        AccessRequest,
        AllyCompany,
        EInvoiceDocument,
        Opportunity,
        Product,
        Project,
        Quote,
        Supplier,
        SupplierItem,
        SupportTicket,
        Survey,
        SurveyPoint,
        User,
    )

    ahora = datetime.now(UTC)
    vend = db.scalar(select(User).where(User.email == "vendedor@ejemplo.com"))
    tec = db.scalar(select(User).where(User.email == "tecnico@ejemplo.com"))
    p_admin = SimpleNamespace(tenant=t, user=admin, ip=None)

    def ticket(id: int, number: str, subject: str, **kw) -> SupportTicket:
        base = {
            "tenant_id": t.id,
            "customer_id": c.id,
            "kind": "soporte",
            "channel": "portal",
            "level": 1,
            "priority": "alta",
            "status": "nuevo",
            "description": "La cámara del portón no da imagen desde anoche.",
            "sla": {"respuesta": 4, "resolucion": 24},
        }
        base.update(kw)
        tk = SupportTicket(id=id, number=number, subject=subject, **base)
        db.add(tk)
        db.flush()
        return tk

    def sla_atrasos(to: str | None = None) -> Callable[[], str]:
        def run() -> str:
            from ..services import sla

            # cada ticket avisa una sola vez por plazo: el segundo tipo (el aviso al responsable) usa sus propios tickets
            base = 812 if to is None else 816
            ticket(base, f"TCK-0{base}", "Cámara sin imagen", due_at=ahora - timedelta(hours=3), assigned_to=tec.id)
            ticket(base + 1, f"TCK-0{base + 1}", "Grabador reinicia solo", priority="media", due_at=ahora - timedelta(hours=1))
            return _queued(db, lambda: sla.avisar_atrasos(db, t, ahora), to)

        return run

    def asignado(con_responsable: bool) -> str:
        from ..routers.support_desk import _avisar_asignado

        n = 821 if con_responsable else 820
        tk = ticket(n, f"TCK-0{n}", "Mantenimiento preventivo de cámaras", assigned_to=tec.id if con_responsable else None)
        return _queued(db, lambda: _avisar_asignado(db, p_admin, tk, nuevo=True))

    def ticket_portal() -> str:
        from ..routers.client_portal import avisar_ticket_portal

        tk = ticket(830, "TCK-0830", "Falla · Cámara del portón sin imagen")
        return _queued(db, lambda: avisar_ticket_portal(db, t, c, tk, tk.description))

    def ticket_aliado() -> str:
        from ..routers.ally_portal import avisar_ticket_aliado

        aliada = db.scalar(select(AllyCompany).where(AllyCompany.tenant_id == t.id))
        tk = ticket(831, "TCK-0831", "Cable dañado en la bodega", ally_company_id=aliada.id)
        return _queued(db, lambda: avisar_ticket_aliado(db, t, aliada, proyecto(), tk))

    def respuesta_cliente() -> str:
        from ..routers.client_portal import avisar_respuesta_cliente

        tk = ticket(832, "TCK-0832", "Grabador sin acceso remoto", status="esperando_cliente", assigned_to=tec.id)
        return _queued(db, lambda: avisar_respuesta_cliente(db, t, tk, "Carla Cliente", "Ya reinicié el grabador y sigue igual."))

    def ticket_web() -> str:
        from ..routers.public_incidents import IncidentIn, avisar_ticket_web

        texto = "Las cámaras de la entrada no graban desde ayer."
        data = IncidentIn(
            name="Carla Compradora",
            company="Cliente Ejemplo S.A.",
            phone="8800-0000",
            email="carla@ejemplo.com",
            kind="falla",
            severity="alta",
            description=texto,
            location="Bodega central",
        )
        tk = ticket(833, "TCK-0833", f"Falla · {texto}", channel="web")
        return _queued(db, lambda: avisar_ticket_web(db, t, tk, data, c, texto))

    def levantamiento(id: int, **kw) -> Survey:
        s = Survey(id=id, tenant_id=t.id, number=f"LEV-0000{id}", customer_id=c.id, site="Bodega central", technician_id=tec.id, kind="cctv", **kw)
        db.add(s)
        db.flush()
        return s

    def lev_asignado() -> str:
        from ..routers.fieldwork import avisar_levantamiento_asignado

        s = levantamiento(21)
        return _queued(db, lambda: avisar_levantamiento_asignado(db, t, tec.email, s, HOY + timedelta(days=2)))

    def lev_enviado() -> str:
        from ..routers.fieldwork import send_survey

        s = levantamiento(22)
        db.add_all([SurveyPoint(survey_id=s.id, code="C1", label="Portón principal"), SurveyPoint(survey_id=s.id, code="C2", label="Bodega")])
        db.flush()
        db.refresh(s)
        return _queued(db, lambda: send_survey(db, t, s, tec, None))

    def lev_devuelto() -> str:
        from ..routers.fieldwork import ObservationIn, _notify_returned

        s = levantamiento(23)
        obs = [ObservationIn(point_code="C1", text="Falta la altura de montaje."), ObservationIn(text="Agregue una foto del rack.")]
        return _queued(db, lambda: _notify_returned(db, p_admin, s, obs))

    def oportunidad() -> Opportunity:
        o = db.get(Opportunity, 40)
        if o is None:
            o = Opportunity(
                id=40,
                tenant_id=t.id,
                number="OPO-000040",
                title="Videovigilancia para bodega",
                customer_id=c.id,
                owner_id=vend.id,
                site="Bodega central",
                solution="cctv",
            )
            db.add(o)
            db.flush()
        return o

    def lev_solicitado() -> str:
        from ..routers.pipeline import avisar_solicitud_levantamiento

        o = oportunidad()
        return _queued(db, lambda: avisar_solicitud_levantamiento(db, t, o, vend.full_name, HOY + timedelta(days=3), "El cliente prefiere en la mañana."))

    def cotizacion_lista() -> str:
        from ..routers.fieldwork import _notify_quote_ready

        o = oportunidad()
        q = SimpleNamespace(id=190, number="COT-000190")
        return _queued(db, lambda: _notify_quote_ready(db, p_admin, o, q))

    def proyecto() -> Project:
        pr = db.get(Project, 9)
        if pr is None:
            pr = Project(id=9, tenant_id=t.id, number="PRY-000009", name="Instalación de videovigilancia · Bodega central", customer_id=c.id)
            db.add(pr)
            db.flush()
        return pr

    def proyecto_terminado() -> str:
        from ..routers.fieldwork import avisar_proyecto_terminado

        pr = proyecto()
        return _queued(db, lambda: avisar_proyecto_terminado(db, t, ("admin",), pr, 12.5))

    def compras_proyecto() -> str:
        from ..routers.projects import avisar_compra_proyecto

        faltan = [
            {"name": "Cámara IP 4 MP", "to_buy": Decimal("2"), "supplier_stock": 15},
            {"name": "Cable UTP Cat 6 (caja)", "to_buy": Decimal("1"), "supplier_stock": None},
        ]
        return _queued(db, lambda: avisar_compra_proyecto(db, t, proyecto(), faltan, {"id": 4, "number": "SC-0004"}))

    def producto() -> Product:
        prod = db.get(Product, 70)
        if prod is None:
            prov = Supplier(tenant_id=t.id, name="Proveedor Ejemplo")
            prod = Product(id=70, tenant_id=t.id, name="Cámara IP 4 MP", code="CAM-4MP", currency="CRC", price=Decimal("45000"), active=True, show_on_web=True)
            db.add_all([prov, prod])
            db.flush()
            db.add(SupplierItem(tenant_id=t.id, supplier_id=prov.id, product_id=prod.id, code="EJ-CAM4", name="Cámara IP 4 MP"))
            db.flush()
        return prod

    def compras_pedido() -> str:
        from ..services.store_inventory import _notify_paid_purchase

        prod = producto()
        o = _order(t, "pagado", paid_at=ahora)
        req = SimpleNamespace(id=3, number="SC-0003", lines=[SimpleNamespace(product_id=prod.id, name=prod.name, quantity=Decimal("2"))])
        return _queued(db, lambda: _notify_paid_purchase(db, t, o, [req]))

    def margen_resumen() -> str:
        from ..tasks.margen import daily_summary

        prod = producto()
        prod.margin_flagged_at = ahora
        prod.margin_info = {"margin_pct": "18", "min_pct": "25", "price": "45000", "suggested": "52000", "currency": "CRC", "flagged": True}
        db.flush()
        return _queued(db, lambda: daily_summary(db))

    def margen_frenado() -> str:
        from ..services.margin_guard import _brake_alert

        out = {"reason": "revisión diaria", "frenada": "el tipo de cambio del dólar saltó 12 % de un día al otro", "nuevos_retenidos": 0, "frenados": 3}
        return _queued(db, lambda: _brake_alert(db, t, out, {}))

    def margen_sonda() -> str:
        from ..services.discounts import MARGIN_PROBE_LIMIT, margin_probe

        p_vend = SimpleNamespace(tenant=t, user=vend, ip=None)
        q = SimpleNamespace(id=191, number="COT-000191")

        def run() -> None:
            for i in range(MARGIN_PROBE_LIMIT):
                margin_probe(db, p_vend, q, Decimal(20 + i), False)

        return _queued(db, run)

    def salud_diaria() -> str:
        from ..tasks.salud import daily_report

        return _queued(db, lambda: daily_report(db, ahora), "admin@ejemplo.com")

    def recurrencia():
        return SimpleNamespace(id=6, tenant_id=t.id, name="Monitoreo mensual", next_date=HOY - timedelta(days=31))

    def recurrencia_alerta() -> str:
        from ..routers.ops import _rec_alert

        cuerpo = "La factura del periodo no salió: el cliente no tiene correo. Agréguelo en su ficha y reenvíela desde Aprobaciones."
        return _queued(db, lambda: _rec_alert(db, recurrencia(), inv, "Factura recurrente sin enviar", cuerpo))

    def recurrencia_atrasada() -> str:
        from ..routers.ops import _rec_behind_alert

        return _queued(db, lambda: _rec_behind_alert(db, recurrencia()))

    def hacienda_rechazo() -> str:
        from ..services.hacienda_sync import _alert_admins

        detalle = "La factura queda como estaba. Detalle: el monto supera el de la factura de referencia."
        return _queued(db, lambda: _alert_admins(db, t, "Hacienda rechazó la nota de crédito 00100001030000000012", detalle))

    def nc_por_recepcion() -> str:
        from ..services.reception_intake import _alert_client_rejection

        doc = EInvoiceDocument(
            tenant_id=t.id,
            invoice_id=601,
            doc_type="FE",
            consecutive="00100001010000000601",
            provider="hacienda",
            clave="506000000000000000000000000000000000000000000000",
            receiver_message="3",
            receiver_detail="El monto no corresponde a lo acordado.",
        )
        return _queued(db, lambda: _alert_client_rejection(db, t, doc))

    def cotizaciones_frias() -> str:
        from ..tasks.ventas import avisar_cotizaciones_frias

        q = Quote(
            id=192,
            tenant_id=t.id,
            customer_id=c.id,
            number="COT-000192",
            status="enviada",
            acceptance_status="pendiente",
            seller_id=vend.id,
            created_by=vend.id,
            currency="CRC",
            total=Decimal("480000"),
            issue_date=HOY - timedelta(days=12),
        )
        db.add(q)
        db.flush()
        return _queued(db, lambda: avisar_cotizaciones_frias(db, t, ahora))

    def acceso_solicitud() -> AccessRequest:
        r = db.get(AccessRequest, 8)
        if r is None:
            r = AccessRequest(
                id=8,
                tenant_id=t.id,
                name="Carla Cliente",
                email="carla@ejemplo.com",
                phone="8800-0000",
                company="Cliente Ejemplo S.A.",
                id_number="3101000000",
                status="pendiente",
            )
            db.add(r)
            db.flush()
        return r

    def acceso_nueva() -> str:
        from ..routers.public_access import avisar_solicitud_acceso

        return _queued(db, lambda: avisar_solicitud_acceso(db, t, acceso_solicitud()))

    def acceso_rechazada() -> str:
        from ..routers.client_access import avisar_rechazo

        return _queued(db, lambda: avisar_rechazo(db, t, acceso_solicitud(), "No encontramos su empresa entre nuestros clientes con contrato vigente."))

    def mensaje_tienda() -> str:
        from ..routers.store import avisar_mensaje_tienda

        return _queued(db, lambda: avisar_mensaje_tienda(db, t, c, "Carla Compradora", "carla@ejemplo.com", "8800-0000", "¿Hacen instalación en Cartago?"))

    def asesoria_web() -> str:
        from ..routers.store_showcase import AdvisoryIn, _crear_oportunidad

        data = AdvisoryIn(
            name="Carla Compradora",
            party="empresa",
            company="Cliente Ejemplo S.A.",
            phone="8800-0000",
            email="carla@ejemplo.com",
            solution="cctv",
            description="Necesitamos cámaras para la bodega y la entrada principal.",
            location="Heredia",
            install=True,
        )
        return _queued(db, lambda: _crear_oportunidad(db, t.slug, data, "127.0.0.1"))

    def contrasena_temporal() -> str:
        from ..routers.users_admin import avisar_contrasena_temporal

        return _queued(db, lambda: avisar_contrasena_temporal(db, t, vend, admin.full_name, ahora))

    def prueba_correo() -> str:
        from ..routers.settings import mail_test

        return _queued(db, lambda: mail_test(p_admin, db))

    return {
        "aviso-interno-ticket-sla": sla_atrasos(),
        "ticket-sla-al-responsable": sla_atrasos("tecnico@ejemplo.com"),
        "ticket-asignado": lambda: asignado(True),
        "ticket-sin-asignar": lambda: asignado(False),
        "ticket-portal-clientes": ticket_portal,
        "ticket-portal-aliados": ticket_aliado,
        "ticket-respuesta-del-cliente": respuesta_cliente,
        "ticket-web": ticket_web,
        "levantamiento-asignado": lev_asignado,
        "levantamiento-enviado-a-revision": lev_enviado,
        "levantamiento-devuelto": lev_devuelto,
        "levantamiento-solicitado": lev_solicitado,
        "cotizacion-lista": cotizacion_lista,
        "proyecto-terminado": proyecto_terminado,
        "proyecto-compras": compras_proyecto,
        "aviso-interno-compras": compras_pedido,
        "aviso-interno-margen": margen_resumen,
        "margen-revision-frenada": margen_frenado,
        "margen-sonda-de-cotizaciones": margen_sonda,
        "aviso-interno-salud": salud_diaria,
        "recurrencia-sin-enviar": recurrencia_alerta,
        "recurrencia-periodos-atrasados": recurrencia_atrasada,
        "hacienda-rechazo": hacienda_rechazo,
        "nota-de-credito-por-recepcion": nc_por_recepcion,
        "cotizaciones-frias": cotizaciones_frias,
        "acceso-portal-solicitud": acceso_nueva,
        "acceso-portal-rechazado": acceso_rechazada,
        "mensaje-desde-la-tienda": mensaje_tienda,
        "asesoria-web": asesoria_web,
        "contrasena-temporal": contrasena_temporal,
        "prueba-de-correo": prueba_correo,
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("Uso: python -m app.tools.preview_emails CARPETA")
        return 2
    os.environ.pop("RESEND_API_KEY", None)  # nunca sale nada: todo queda simulado en la base en memoria
    out = Path(args[0])
    out.mkdir(parents=True, exist_ok=True)
    db = _session()
    try:
        t, admin, c = _seed(db)
        hechos, fallas = [], []
        for name, build in samples(db, t, admin, c).items():
            try:
                body = build()
            except Exception as e:  # noqa: BLE001 - un tipo que falle no frena los demas
                fallas.append(f"{name}: {type(e).__name__}: {e}")
                continue
            page = f'<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(name)}</title></head><body style="margin:0">{body}</body></html>'
            (out / f"{name}.html").write_text(page, encoding="utf-8")
            hechos.append(name)
        enlaces = "".join(f'<li><a href="{n}.html">{html.escape(n)}</a></li>' for n in hechos)
        (out / "index.html").write_text(
            f'<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Correos</title></head><body style="font-family:Arial,sans-serif;padding:24px"><h1>Correos del sistema (ejemplo)</h1><ul>{enlaces}</ul></body></html>',
            encoding="utf-8",
        )
    finally:
        db.rollback()
        db.close()
    print(f"{len(hechos)} correo(s) en {out.resolve()}")
    for f in fallas:
        print(f"  FALLO {f}")
    return 1 if fallas else 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
