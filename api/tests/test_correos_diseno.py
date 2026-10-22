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
    assert re.search(r'<img src="https?://[^"]+/media/logo.png" alt="[^"]+" width="\d+"', html)  # logo absoluto, con alt y ancho
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
    r = queue_email(db_session, t, "y@ejemplo.com", "Creá tu contraseña", reset_email_html(t, "Ana", f"https://p.ejemplo.com/restablecer/{tok}", "1 hora"), "password_reset", 1)
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


def test_vista_previa_genera_un_html_por_tipo(tmp_path, monkeypatch):
    from app.tools import preview_emails

    monkeypatch.setenv("RESEND_API_KEY", "no-se-usa")
    assert preview_emails.main([str(tmp_path)]) == 0
    hechos = {p.stem for p in tmp_path.glob("*.html")}
    for k in ("cotizacion", "factura-con-pago", "cobro-atraso", "pedido-recibido", "invitacion-panel", "restablecer-contrasena", "acceso-portal-aliados", "cola-por-emitir"):
        assert k in hechos
    assert "index" in hechos
    assert all(el.MARKER in (tmp_path / f"{k}.html").read_text(encoding="utf-8") for k in hechos - {"index"})
