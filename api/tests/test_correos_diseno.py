"""Diseno base de los correos (email_layout): un solo marco para todo lo que manda el sistema, que se vea bien en el
telefono y en Outlook/Hotmail, con UN boton principal y su enlace en texto, y el pie con el contacto de la empresa."""

from __future__ import annotations

import re

from sqlalchemy import select

from app.models import Tenant
from app.services import email_layout as el


def _tenant(db) -> Tenant:
    return db.scalars(select(Tenant).order_by(Tenant.id)).first()


def _sin_css_raro(html: str) -> None:
    # Outlook (motor de Word) y Hotmail: nada de hojas de estilo, flex ni grid; solo Arial
    assert "<style" not in html and "<link" not in html
    assert "display:flex" not in html and "display:grid" not in html
    fuentes = set(re.findall(r"font-family:([^;\"']+)", html))
    assert fuentes == {el.FONT}, fuentes


def test_marco_completo(client, db_session):
    t = _tenant(db_session)
    t.settings = {**(t.settings or {}), "phones": [{"number": "2222-0000", "kind": "whatsapp", "main": True}], "addresses": ["San José, Costa Rica"]}
    t.settings = {**t.settings, "store": {**(t.settings.get("store") or {}), "primary": "#123456", "logo_url": "/media/logo.png", "whatsapp": "8888-0000"}}
    db_session.flush()
    html = el.render(t, title="Pasó algo", body=el.data_rows([("Número", "FEC-1"), ("Vacío", "")], total=("Total", "₡1.00")), action=("https://x.ejemplo.com/a?b=1&c=2", "Ver"))
    assert el.MARKER in html and "max-width:600px" in html and 'width="600"' in html  # 600 px, tambien en Outlook
    assert "#123456" in html  # el primario de Mi Tienda
    # logo absoluto, con alt y alto fijo (Outlook ignora height:auto: un logo cuadrado nunca sale de 160 px de alto)
    assert re.search(r'<img src="https?://[^"]+/media/logo.png" alt="[^"]+" height="40" style="[^"]*height:40px;width:auto;max-width:220px', html)
    assert "display:none" in html and "mso-hide:all" in html  # preheader oculto
    assert "info@crimsoncr.com" in html and "2222-0000" in html and "8888-0000" in html and "San José, Costa Rica" in html
    assert "Vacío" not in html  # las filas sin valor no salen
    # UN boton y su enlace en texto (escapado) como respaldo: el href del boton, el del texto y el texto visible
    assert html.count('bgcolor="#123456"') == 1 and html.count("https://x.ejemplo.com/a?b=1&amp;c=2") == 3
    _sin_css_raro(html)


def test_sin_logo_va_el_nombre_y_el_contacto_por_defecto(client, db_session):
    t = _tenant(db_session)
    html = el.render(t, title="Hola", body=el.p("<b>texto</b>"))
    assert "<img" not in html and t.name in html and "info@crimsoncr.com" in html
    assert "&lt;b&gt;texto&lt;/b&gt;" in html  # el texto plano se escapa
    assert el.PRIMARY in html


def test_ensure_envuelve_fragmentos_una_vez(client, db_session):
    t = _tenant(db_session)
    frag = '<p>Algo pasó con <b>X</b>.</p><blockquote>nota</blockquote><p><a href="https://p.ejemplo.com/tickets?id=3">Abrir el ticket</a></p>'
    html = el.ensure(t, "Ticket TCK-3 atrasado · Empresa", frag)
    assert el.MARKER in html and ">Ticket TCK-3 atrasado</h1>" in html  # el asunto (sin la empresa) como titulo
    assert html.count("https://p.ejemplo.com/tickets?id=3") == 3 and "Abrir el ticket" in html  # boton + enlace en texto
    assert "<blockquote style=" in html and "<p>" not in html  # estilos en linea
    assert el.ensure(t, "otro", html) == html  # no se envuelve dos veces
    doc = "<!doctype html><html><body>informe</body></html>"
    assert el.ensure(t, "Informe", doc) == doc  # un documento completo no se toca
    _sin_css_raro(html)


def test_queue_email_guarda_el_marco_y_oculta_los_enlaces_sensibles(client, db_session):
    from app.services.accounts import reset_email_html
    from app.services.mail import queue_email

    t = _tenant(db_session)
    m = queue_email(db_session, t, "x@ejemplo.com", "Aviso", "<p>hola</p>", "preview", 0)
    assert el.MARKER in m.html
    tok = "A" * 40
    r = queue_email(db_session, t, "y@ejemplo.com", "Cree su contraseña", reset_email_html(t, "Ana", f"https://p.ejemplo.com/restablecer/{tok}", "1 hora"), "password_reset", 1)
    assert tok not in r.html and r.html.count("/restablecer/[oculto]") == 3  # boton y enlace en texto, todos ocultos
    assert "Restablecer contraseña" in r.html


def test_correo_de_documento_escapa_el_mensaje(client, db_session):
    from app.services.mail import doc_email_html

    t = _tenant(db_session)
    html = doc_email_html(t, "factura", "FEC-9", "₡10.00", "https://p.ejemplo.com/pagar/t", '<a href="https://phish.ejemplo.com">clic</a>', due="01/11/2026")
    assert '<a href="https://phish' not in html and "&lt;a href=" in html
    assert "Ver y pagar en línea" in html and "Vence" in html and "01/11/2026" in html
    cot = doc_email_html(t, "cotización", "COT-1", "₡10.00", None, None, due="01/11/2026")
    assert "Válida hasta" in cot and "Ver y pagar" not in cot  # la cotizacion va en PDF: sin boton de pago
    # con acceso al portal de clientes: "Ver cotización" (y "Ver factura" sin enlace de pago), con su enlace en texto
    con = doc_email_html(t, "cotización", "COT-1", "₡10.00", None, None, view_link="https://p.ejemplo.com/documentos")
    assert ">Ver cotización</a>" in con and con.count("https://p.ejemplo.com/documentos") == 3
    fac = doc_email_html(t, "factura", "FEC-1", "₡10.00", None, None, view_link="https://p.ejemplo.com/documentos")
    assert ">Ver factura</a>" in fac
    # el enlace de pago manda sobre el del portal
    pago = doc_email_html(t, "factura", "FEC-1", "₡10.00", "https://p.ejemplo.com/pagar/t", None, view_link="https://p.ejemplo.com/documentos")
    assert "Ver y pagar en línea" in pago and "/documentos" not in pago


def test_boton_ver_documento_solo_con_acceso_al_portal(client, db_session):
    from app.core.config import settings
    from app.models import Customer, Invoice, Quote, TenantUser, User
    from app.services.documents_send import portal_link

    t = _tenant(db_session)
    c = Customer(tenant_id=t.id, name="Cliente Portal", email="portal@ejemplo.com")
    db_session.add(c)
    db_session.flush()
    q = Quote(tenant_id=t.id, customer_id=c.id, number="COT-P1")
    assert portal_link(db_session, t, q, c) is None  # sin usuario del portal: sin boton (va en PDF)
    u = User(email="portal-usuario@ejemplo.com", full_name="Usuario Portal", password_hash="x")
    db_session.add(u)
    db_session.flush()
    m = TenantUser(tenant_id=t.id, user_id=u.id, role_code="cliente_usuario", active=True, customer_id=c.id)
    db_session.add(m)
    db_session.flush()
    assert portal_link(db_session, t, q, c) is None  # cliente_usuario no ve documentos
    m.role_code = "cliente_admin"
    db_session.flush()
    assert portal_link(db_session, t, q, c) == f"{settings.public_base_url.rstrip('/')}/documentos"
    assert portal_link(db_session, t, Invoice(tenant_id=t.id, customer_id=c.id, number="FEC-P1", status="anulada"), c) is None
    m.active = False
    db_session.flush()
    assert portal_link(db_session, t, q, c) is None


def test_marco_sin_base_para_el_respaldo(client):
    from app.tools.respaldo_diario import Fallo, html_falla

    html = html_falla([Fallo(5, "subida", "rclone <falló>")], {"subido": True, "archivo": "x.zip.age"}, __import__("datetime").date(2026, 10, 21))
    assert el.MARKER in html and "max-width:600px" in html and "info@crimsoncr.com" in html and "display:none" in html
    assert "rclone &lt;falló&gt;" in html and "sí se subió" in html
    _sin_css_raro(html)


def test_aviso_del_webhook_de_onvo_con_el_diseno_base(client, db_session):
    from app.services import onvo

    t = _tenant(db_session)
    titulo, html = onvo.webhook_alert_html(t, 401, "detalle <b>x</b>")
    assert "secreto" in titulo and el.MARKER in html and "&lt;b&gt;x&lt;/b&gt;" in html
    assert "/ajustes?tab=pagos" in html and "#5a5560" not in html
    _sin_css_raro(html)


def test_lone_link_con_punto_final_es_el_boton(client, db_session):
    t = _tenant(db_session)
    html = el.ensure(t, "Aviso", '<p>Hola.</p><p><a href="https://p.ejemplo.com/x">Crear</a> (ya viene listo).</p>')
    assert html.count("https://p.ejemplo.com/x") == 3 and "(ya viene listo)" in html


def test_vista_previa_genera_un_html_por_tipo(tmp_path, monkeypatch):
    from app.tools import preview_emails

    monkeypatch.setenv("RESEND_API_KEY", "no-se-usa")
    assert preview_emails.main([str(tmp_path)]) == 0
    hechos = {p.stem for p in tmp_path.glob("*.html")}
    esperados = (
        "cotizacion",
        "cotizacion-con-portal",
        "factura-con-pago",
        "cobro-atraso",
        "cobro-recordatorios-sin-salir",
        "cartera-semanal",
        "pedido-recibido",
        "pedido-por-vencer",
        "pedido-preparando",
        "pedido-listo-para-retiro",
        "pedido-entregado",
        "pedido-cancelado",
        "pedido-reembolsado",
        "ya-hay-existencia",
        "entradas-evento",
        "invitacion-panel",
        "restablecer-contrasena",
        "acceso-portal-aliados",
        "aliado-trabajo-asignado",
        "aliado-solicitud-de-precios",
        "venta-cotizacion-aceptada",
        "venta-orden-asignada",
        "recurrencias-proximas",
        "correos-que-no-llegaron",
        "comprobante-entrega-fallida",
        "hacienda-alerta-diaria",
        "onvo-webhook-secreto",
        "onvo-webhook-sin-confirmar",
        "cola-por-emitir",
        "informe-preliminar",
        "respaldo-diario-falla",
        # avisos internos armados por la funcion real de cada uno (no fragmentos escritos en la herramienta)
        "aviso-interno-ticket-sla",
        "ticket-sla-al-responsable",
        "aviso-interno-compras",
        "aviso-interno-salud",
        "aviso-interno-margen",
        "margen-revision-frenada",
        "ticket-asignado",
        "ticket-portal-clientes",
        "ticket-portal-aliados",
        "ticket-respuesta-del-cliente",
        "ticket-web",
        "levantamiento-enviado-a-revision",
        "levantamiento-devuelto",
        "cotizacion-lista",
        "proyecto-terminado",
        "proyecto-compras",
        "acceso-portal-solicitud",
        "acceso-portal-rechazado",
        "mensaje-desde-la-tienda",
        "recurrencia-sin-enviar",
        "recurrencia-periodos-atrasados",
        "hacienda-rechazo",
        "nota-de-credito-por-recepcion",
        "cotizaciones-frias",
        "margen-sonda-de-cotizaciones",
        "asesoria-web",
        "contrasena-temporal",
        "prueba-de-correo",
    )
    faltan = [k for k in esperados if k not in hechos]
    assert not faltan, faltan
    assert "index" in hechos
    assert all(el.MARKER in (tmp_path / f"{k}.html").read_text(encoding="utf-8") for k in hechos - {"index"})
    # salen de la funcion real con los datos sembrados: el codigo del proveedor, los tickets atrasados, el bloque de Salud
    leer = lambda k: (tmp_path / f"{k}.html").read_text(encoding="utf-8")  # noqa: E731
    assert "EJ-CAM4" in leer("aviso-interno-compras")
    assert "TCK-0812" in leer("aviso-interno-ticket-sla") and "TCK-0816" in leer("ticket-sla-al-responsable")
    assert "Tareas automáticas" in leer("aviso-interno-salud")
    assert "sugerido" in leer("aviso-interno-margen")
