"""Vista previa de los correos del sistema: un .html por tipo de correo, con datos de EJEMPLO, para revisarlos en el
navegador (y en el telefono, achicando la ventana) antes de publicar un cambio de diseno.

Uso:  python -m app.tools.preview_emails CARPETA

- Usa una base SQLite en memoria propia (nunca la de la plataforma) y quita RESEND_API_KEY del proceso: no sale ningun
  correo, todo queda "simulado" en esa base desechable.
- Los datos son inventados (Empresa Ejemplo, Cliente Ejemplo, correos @ejemplo.com): nada de clientes reales.
- Llama a las mismas funciones que arman los correos reales (doc_email_html, reminder_email, el correo del pedido web,
  invitaciones, avisos internos...), asi que lo que se ve es lo que sale.
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
            "store": {"whatsapp": "8888-0000"},
        },
    )
    db.add(t)
    db.flush()
    admin = User(email="admin@ejemplo.com", full_name="Ana Administradora", password_hash="x")
    vend = User(email="vendedor@ejemplo.com", full_name="Víctor Vendedor", password_hash="x")
    db.add_all([admin, vend])
    db.flush()
    db.add_all(
        [
            TenantUser(tenant_id=t.id, user_id=admin.id, role_code="admin", active=True),
            TenantUser(tenant_id=t.id, user_id=vend.id, role_code="ventas", active=True),
        ]
    )
    c = Customer(tenant_id=t.id, name="Cliente Ejemplo S.A.", email="cliente@ejemplo.com", id_type="juridica", id_number="3101000000")
    db.add(c)
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


def _outbox_html(db, before: int) -> str:
    from sqlalchemy import select

    from ..models import EmailOutbox

    m = db.scalars(select(EmailOutbox).where(EmailOutbox.id > before).order_by(EmailOutbox.id)).first()
    if m is None:
        raise RuntimeError("no se encolo ningun correo")
    return m.html


def _last_id(db) -> int:
    from sqlalchemy import func, select

    from ..models import EmailOutbox

    return db.scalar(select(func.max(EmailOutbox.id))) or 0


def _queued(db, fn: Callable[[], object]) -> str:
    """Corre una funcion que encola un correo y devuelve el HTML que quedo en la cola (lo que sale de verdad)."""
    antes = _last_id(db)
    fn()
    db.flush()
    return _outbox_html(db, antes)


def samples(db, t, admin, c) -> dict[str, Callable[[], str]]:
    from ..services import accounts, email_layout, onvo
    from ..services import store_orders as so
    from ..services.einvoice_delivery import _html as einvoice_html
    from ..services.mail import doc_email_html, queue_email
    from ..tasks import cobranza
    from ..tasks.fiscal import digest_html

    inv = _invoice(t, c)
    plan = _invoice(t, c, number="FEC-000502", external_order="MENSUALIDAD-PLATAFORMA", total=Decimal("45000"), balance=Decimal("45000"))
    link = "https://panel.ejemplo.com/pagar/TOKEN-DE-EJEMPLO"

    def recordatorio(dias: int, factura=inv) -> str:
        factura.due_date = HOY - timedelta(days=dias)
        return cobranza.reminder_email(t, factura, dias, HOY, link, attached=True)[1]

    def pedido(kind: str, status: str, **kw) -> str:
        return so._email_html(t, _order(t, status, **kw), kind)

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
            lambda: accounts.notify_security(db, t, admin, "Tu contraseña de Crimson cambió", "<p>Si no fuiste vos, avisá de inmediato a la administración.</p>"),
        )

    def por_emitir() -> str:
        fila = _invoice(t, c, customer_id=None, status="enviada")
        return digest_html(db, t, [fila], 1, HOY, 2, "ninguno")

    def aviso_fragmento(asunto: str, cuerpo: str) -> Callable[[], str]:
        # un aviso interno armado como fragmento (notify_roles / queue_email): queue_email le pone el diseno base
        return lambda: _queued(db, lambda: queue_email(db, t, "admin@ejemplo.com", asunto, cuerpo, "preview", 0))

    return {
        "cotizacion": lambda: doc_email_html(t, "cotización", "COT-000188", "₡226,000.00", None, "Gracias por su interés. Quedamos atentos.", issued="21/10/2026", due="05/11/2026"),
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
        "pedido-aviso-interno": interno_pedido,
        "invitacion-panel": lambda: accounts.invite_email_html(t, "ventas", "https://panel.ejemplo.com/invitacion/TOKEN-DE-EJEMPLO", 7),
        "restablecer-contrasena": lambda: accounts.reset_email_html(t, "Ana Administradora", "https://panel.ejemplo.com/restablecer/TOKEN-DE-EJEMPLO", "24 horas"),
        "aviso-de-seguridad": seguridad,
        "acceso-portal-aliados": lambda: accounts.access_email_html(
            t,
            title="Su acceso al portal de aliados",
            name="Ernesto Aliado",
            intro_html="Empresa Ejemplo le dio acceso al portal de aliados como Administrador de <b>Aliado Ejemplo S.A.</b>.",
            link="https://panel.ejemplo.com/invitacion/TOKEN-DE-EJEMPLO",
            label="Entrar al portal",
            note="Con el botón crea su contraseña y entra. El enlace vence en 7 días y sirve una sola vez.",
            vos=False,
        ),
        "acceso-portal-clientes": lambda: accounts.access_email_html(
            t,
            title="Su acceso al portal de clientes",
            name="Carla Cliente",
            intro_html="Empresa Ejemplo le dio acceso al portal de clientes de <b>Cliente Ejemplo S.A.</b> (Usuario).",
            link="https://panel.ejemplo.com/invitacion/TOKEN-DE-EJEMPLO",
            label="Crear mi contraseña",
            note="El enlace vence en 7 días y sirve una sola vez. Si no esperaba este acceso, ignore este correo.",
            vos=False,
        ),
        "comprobante-electronico": lambda: einvoice_html(
            t,
            f"Factura electrónica {inv.number}",
            [f"Le compartimos la <b>factura electrónica {inv.number}</b> (clave 506000000000000000000000000000000000000000000000).", "Adjuntamos el XML y el PDF."],
            True,
        ),
        "recibo-pago-en-linea": recibo_onvo,
        "alerta-pago-onvo": alerta_onvo,
        "cola-por-emitir": por_emitir,
        "aviso-interno-ticket-sla": aviso_fragmento(
            "2 ticket(s) sin primera respuesta a tiempo",
            "<p>Estos tickets están sin primera respuesta a tiempo:</p><ul><li>TCK-0012 · Cámara sin imagen · prioridad alta</li>"
            "<li>TCK-0015 · Grabador reinicia · prioridad media</li></ul>" + email_layout.cta("/tickets", "Abrir Tickets"),
        ),
        "aviso-interno-compras": aviso_fragmento(
            "PAGADO: comprar hoy a Proveedor Ejemplo · pedido TW-000077 (SC-0003)",
            "<p>El pedido web <b>TW-000077</b> ya está <b>pagado</b>. Hay que comprarle hoy a Proveedor Ejemplo:</p>"
            "<ul><li><b>Cámara IP 4 MP</b> × 2 · código CAM-4MP</li></ul>" + email_layout.cta("/compras", "Abrir Compras"),
        ),
        "aviso-interno-salud": aviso_fragmento(
            "Salud de la plataforma · 2026-10-21 · OK",
            "<h3>Salud de la plataforma · 2026-10-21 · OK</h3><table border=0 cellpadding=4><tr><td>Latido del worker</td><td>hace 1 min</td></tr>"
            "<tr><td>Correos de las últimas 24 h</td><td>42 enviados</td></tr></table>",
        ),
        "aviso-interno-margen": aviso_fragmento(
            "Margen: 1 producto(s) bajo el 25 %",
            "<p>1 producto(s) de la tienda están bajo el margen mínimo de 25 %.</p><ul><li><b>Cámara IP 4 MP</b> (CAM-4MP) · margen 18 %</li></ul>"
            '<p><a href="https://panel.ejemplo.com/aprobaciones?seccion=precios_por_revisar">Revisar precios</a></p>',
        ),
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
