"""Diseno base de TODOS los correos que manda el sistema (cliente, aliado, equipo).

Un solo marco para la cotizacion y la factura, los recordatorios de cobro, la mensualidad de aliados, las invitaciones
y avisos de cuenta, el acceso a los portales, los avisos internos y los del pedido web. Salio de la plantilla del
pedido web (reunion 21/10, items 40 y 49) y se saco aqui para que todos se vean igual.

Reglas de construccion (Outlook de escritorio usa el motor de Word y Hotmail limpia mucho):
- tablas con estilos en linea; nada de hojas de estilo externas, flex ni grid;
- ancho maximo 600 px (tabla "fantasma" para Outlook) y 100 % en el telefono;
- solo Arial (la marca usa solo Arial);
- colores de la empresa: el primario de Mi Tienda (por defecto #e2233a) y el oscuro #15131a;
- encabezado con el logo (Mi Tienda o el de la empresa) con alt y alto fijo; si no hay logo, el nombre;
- UN boton principal por correo (una tabla con fondo: Outlook ignora el padding de un <a>) y el mismo enlace en texto
  como respaldo debajo;
- texto "preheader" oculto (lo que el buzon muestra junto al asunto);
- pie con el correo de contacto (info@crimsoncr.com si la empresa no puso otro), telefono/WhatsApp y direccion.

`render()` arma el correo completo. Los bloques (`p`, `data_rows`, `note`, `items`, ...) devuelven HTML listo: el texto
que reciben se escapa (un nombre o un mensaje que escribe un usuario nunca entra como HTML). Lo que ya es HTML se pasa
por los parametros `*_html` y quien llama responde por haberlo escapado.

`ensure()` es la red de seguridad de mail.queue_email: un aviso que todavia arme su HTML a mano (un parrafo suelto) sale
dentro del marco con el asunto como titulo. Un documento completo (<html>, p. ej. el informe preliminar) no se toca.
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import Tenant

PRIMARY = "#e2233a"  # primario por defecto de Mi Tienda
INK = "#15131a"  # oscuro de la marca
GREY = "#6b6570"
LINE = "#ebe7e3"
SOFT = "#f6f2ee"
BG = "#f4f2f0"
WARN_BG, WARN_INK = "#fff4d6", "#7a5a00"
DANGER = "#b42318"
FONT = "Arial,Helvetica,sans-serif"
WIDTH = 600
CONTACT_EMAIL = "info@crimsoncr.com"
# logo del encabezado: alto fijo (atributo y estilo: Outlook de escritorio ignora height:auto y max-width) y ancho
# proporcional con tope. Un logo cuadrado o vertical queda de 40 px de alto, nunca de 160 px.
LOGO_HEIGHT = 40
LOGO_MAX_WIDTH = 220
# marca del marco: ensure() no envuelve dos veces un correo que ya salio de render()
MARKER = "data-crimson-email"
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_FULL_DOC = re.compile(r"^\s*(<!doctype|<html)", re.I)

e = _html.escape


@dataclass(frozen=True)
class Brand:
    name: str
    primary: str
    logo: str | None
    email: str
    phone: str | None
    whatsapp: str | None
    address: str | None


def _store_cfg(t: Tenant) -> dict:
    from ..routers.store import store_cfg

    return store_cfg(t)


def _absolute(raw) -> str | None:
    """Un correo no resuelve rutas relativas: /media/... se completa con la URL publica; otra cosa no sirve."""
    from ..core.config import settings

    u = str(raw or "").strip()
    if u.startswith("/") and not u.startswith("//"):
        u = f"{settings.public_base_url.rstrip('/')}{u}"
    return u if u.startswith(("https://", "http://")) else None


def logo_url(t: Tenant) -> str | None:
    """Logo de la tienda (Mi Tienda) o el de la empresa, como URL absoluta."""
    for raw in (_store_cfg(t).get("logo_url"), getattr(t, "logo_url", None)):
        u = _absolute(raw)
        if u:
            return u
    return None


def _phone(st: dict) -> str | None:
    phones = [x for x in (st.get("phones") or []) if x]
    if not phones:
        return None
    main = next((x for x in phones if isinstance(x, dict) and x.get("main")), phones[0])
    num = main.get("number") if isinstance(main, dict) else main
    return str(num or "").strip() or None


def _address(t: Tenant) -> str | None:
    """Ajustes -> direcciones (la primera) o, si no hay, la direccion fiscal (Ajustes -> Hacienda: otras senas)."""
    st = t.settings or {}
    for a in st.get("addresses") or []:
        if isinstance(a, dict):
            a = a.get("line") or a.get("address") or a.get("text") or a.get("label")
        a = str(a or "").strip()
        if a:
            return a
    from sqlalchemy import select
    from sqlalchemy.orm import object_session

    s = object_session(t)
    if s is None or getattr(t, "id", None) is None:
        return None
    try:
        from ..models import FiscalProfile

        with s.no_autoflush:
            prof = s.scalar(select(FiscalProfile).where(FiscalProfile.tenant_id == t.id))
    except Exception:  # noqa: BLE001 - el pie nunca tumba un correo
        return None
    if prof is None:
        return None
    partes = [str(x).strip() for x in (prof.other_signs, prof.district_name) if x and str(x).strip()]
    return ", ".join(partes) or None


def brand(t: Tenant) -> Brand:
    st = t.settings or {}
    cfg = _store_cfg(t)
    primary = str(cfg.get("primary") or "").strip()
    phone = _phone(st)
    wa = str(cfg.get("whatsapp") or "").strip() or None
    return Brand(
        name=str(cfg.get("name") or t.name or "").strip(),
        primary=primary if _HEX.match(primary) else PRIMARY,
        logo=logo_url(t),
        email=str(st.get("contact_email") or "").strip() or CONTACT_EMAIL,
        phone=phone,
        whatsapp=wa,
        address=_address(t),
    )


# ---------- bloques ----------
def p(text: str, *, muted: bool = False, small: bool = False) -> str:
    """Parrafo de texto plano (se escapa); conserva los saltos de linea."""
    return p_html(e(text or "").replace("\n", "<br>"), muted=muted, small=small)


def p_html(inner: str, *, muted: bool = False, small: bool = False) -> str:
    """Parrafo con HTML ya escapado por quien llama (negritas, el numero del documento)."""
    size = "13px" if small else "15px"
    color = GREY if muted or small else INK
    return f'<p style="margin:0 0 12px;font-family:{FONT};font-size:{size};line-height:1.55;color:{color}">{inner}</p>'


def heading(text: str) -> str:
    """Subtitulo de una seccion (Resumen, Detalle)."""
    return (
        f'<p style="margin:18px 0 6px;font-family:{FONT};font-size:12px;letter-spacing:.06em;text-transform:uppercase;'
        f'font-weight:700;color:{GREY}">{e(text)}</p>'
    )


def data_rows(rows, *, total: tuple[str, str] | None = None) -> str:
    """Bloque de datos (numero, monto, fecha...): etiqueta a la izquierda, valor a la derecha. Texto plano; las filas
    con valor vacio no salen. `total` va al final, mas grande."""
    filas = [(str(k), str(v)) for k, v in rows if v not in (None, "")]
    if not filas and not total:
        return ""
    td = f"font-family:{FONT};font-size:14px;line-height:1.4;padding:9px 14px;border-bottom:1px solid {LINE}"
    out = "".join(
        f'<tr><td valign="top" style="{td};color:{GREY}">{e(k)}</td>'
        f'<td align="right" valign="top" style="{td};color:{INK};font-weight:700">{e(v)}</td></tr>'
        for k, v in filas
    )
    if total:
        big = f"font-family:{FONT};font-size:17px;font-weight:700;padding:11px 14px;color:{INK}"
        out += f'<tr><td style="{big}">{e(total[0])}</td><td align="right" style="{big};white-space:nowrap">{e(total[1])}</td></tr>'
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{SOFT}" '
        f'style="width:100%;margin:6px 0 16px;background:{SOFT};border-radius:10px;border-collapse:separate">{out}</table>'
    )


def table(head: list[str], rows: list[list[str]], *, right: tuple[int, ...] = ()) -> str:
    """Tabla de detalle (avisos internos: facturas, productos, tickets). Texto plano en cada celda."""
    th = f"font-family:{FONT};font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:{GREY};padding:8px 6px;border-bottom:2px solid {LINE}"
    td = f"font-family:{FONT};font-size:13px;line-height:1.4;color:{INK};padding:8px 6px;border-bottom:1px solid {LINE}"
    cab = "".join(f'<th align="{"right" if i in right else "left"}" style="{th}">{e(h)}</th>' for i, h in enumerate(head))
    cuerpo = "".join(
        "<tr>" + "".join(f'<td align="{"right" if i in right else "left"}" valign="top" style="{td}">{e(str(c))}</td>' for i, c in enumerate(r)) + "</tr>"
        for r in rows
    )
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;margin:4px 0 16px"><tr>{cab}</tr>{cuerpo}</table>'


def items(lines: list[str]) -> str:
    """Lista con vinetas (texto plano). Una tabla y no <ul>: Outlook le pone margenes propios a las listas."""
    filas = "".join(
        f'<tr><td valign="top" width="16" style="font-family:{FONT};font-size:14px;line-height:1.5;color:{GREY};padding:2px 0">&#8226;</td>'
        f'<td style="font-family:{FONT};font-size:14px;line-height:1.5;color:{INK};padding:2px 0">{e(x)}</td></tr>'
        for x in lines
    )
    return f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 14px">{filas}</table>' if filas else ""


def note(text: str | None = None, *, html: str | None = None, warn: bool = False) -> str:
    """Recuadro suave: el mensaje de quien envia, instrucciones de pago, una advertencia."""
    inner = html if html is not None else e(text or "").replace("\n", "<br>")
    if not inner:
        return ""
    bg, ink = (WARN_BG, WARN_INK) if warn else (SOFT, INK)
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;margin:4px 0 16px"><tr>'
        f'<td bgcolor="{bg}" style="background:{bg};border-radius:10px;padding:14px 16px;font-family:{FONT};font-size:14px;line-height:1.55;color:{ink}">{inner}</td>'
        "</tr></table>"
    )


def pill(text: str, color: str | None = None) -> str:
    c = color or PRIMARY
    return (
        f'<span style="display:inline-block;padding:3px 10px;border-radius:999px;border:1px solid {e(c)};color:{e(c)};'
        f'font-family:{FONT};font-size:13px;font-weight:700">{e(text)}</span>'
    )


def button(url: str, label: str, color: str = PRIMARY) -> str:
    """El boton principal "a prueba de Outlook": una tabla con fondo."""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:20px 0 8px"><tr>'
        f'<td align="center" bgcolor="{e(color)}" style="border-radius:10px;background:{e(color)}">'
        f'<a href="{e(url)}" target="_blank" style="display:inline-block;padding:13px 24px;font-family:{FONT};'
        f'font-size:15px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:10px">{e(label)}</a></td></tr></table>'
    )


def link(url: str, label: str, color: str = PRIMARY) -> str:
    """Enlace secundario (texto), para cuando el correo ya tiene su boton principal."""
    return f'<a href="{e(url)}" target="_blank" style="color:{e(color)};font-weight:700;text-decoration:underline">{e(label)}</a>'


def fallback(url: str, vos: bool = False) -> str:
    """El enlace del boton en texto, por si el boton no abre (algunos buzones bloquean los botones)."""
    copie = "copiá" if vos else "copie"
    return (
        f'<p style="margin:10px 0 0;font-family:{FONT};font-size:12px;line-height:1.5;color:{GREY}">Si el botón no abre, {copie} este enlace en el navegador:<br>'
        f'<a href="{e(url)}" target="_blank" style="color:{GREY};word-break:break-all">{e(url)}</a></p>'
    )


def panel_url(path: str) -> str:
    """Una pantalla del panel como URL absoluta (los correos no resuelven rutas relativas)."""
    from ..core.config import settings

    return f"{settings.public_base_url.rstrip('/')}{path}"


def cta(path_or_url: str, label: str) -> str:
    """La llamada a la accion de un aviso armado como fragmento: un parrafo que es solo un enlace. ensure() lo convierte
    en el boton principal del correo (y su enlace en texto)."""
    url = path_or_url if path_or_url.startswith(("http://", "https://")) else panel_url(path_or_url)
    return f'<p><a href="{e(url)}">{e(label)}</a></p>'


def _preheader(text: str) -> str:
    # oculto en el cuerpo, visible en la lista del buzon; el relleno evita que el buzon complete con el texto del correo
    return (
        '<div style="display:none;font-size:1px;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;mso-hide:all;color:#ffffff">'
        f"{e(text)}{'&#8202;&#8203;' * 40}</div>"
    )


def _header(b: Brand) -> str:
    if b.logo:
        marca = (
            f'<img src="{e(b.logo)}" alt="{e(b.name)}" height="{LOGO_HEIGHT}" '
            f'style="display:block;height:{LOGO_HEIGHT}px;width:auto;max-width:{LOGO_MAX_WIDTH}px;border:0;outline:none;text-decoration:none">'
        )
    else:
        marca = f'<span style="font-family:{FONT};font-size:20px;font-weight:700;color:{INK}">{e(b.name)}</span>'
    return f'<tr><td style="padding:20px 28px;border-bottom:3px solid {e(b.primary)}">{marca}</td></tr>'


def _footer(b: Brand, foot: str) -> str:
    tel = []
    if b.phone and b.whatsapp and b.phone.replace(" ", "") == b.whatsapp.replace(" ", ""):
        tel.append(f"Tel. y WhatsApp {e(b.phone)}")
    else:
        if b.phone:
            tel.append(f"Tel. {e(b.phone)}")
        if b.whatsapp:
            tel.append(f"WhatsApp {e(b.whatsapp)}")
    contacto = " · ".join([e(b.email), *tel])
    lineas = [f'<b style="color:{INK}">{e(b.name)}</b>', contacto]
    if b.address:
        lineas.append(e(b.address))
    nota = f'<p style="margin:0 0 10px;font-family:{FONT};font-size:12px;line-height:1.5;color:{GREY}">{foot}</p>' if foot else ""
    return (
        f'<tr><td style="padding:18px 28px 24px;border-top:1px solid {LINE};font-family:{FONT};font-size:12px;line-height:1.6;color:{GREY}">'
        f'{nota}{"<br>".join(lineas)}</td></tr>'
    )


def render(
    t: Tenant,
    *,
    title: str,
    body: str,
    preheader: str | None = None,
    action: tuple[str, str] | None = None,
    after_action: str = "",
    foot: str = "",
    vos: bool = False,
    show_link: bool = True,
) -> str:
    """El correo completo.
    title: lo que paso, corto (va como h1). body: HTML ya armado con los bloques de este modulo.
    action: (url, texto) del UN boton principal; su enlace va tambien en texto debajo (show_link).
    after_action: HTML que va entre el boton y el enlace en texto (p. ej. un enlace secundario).
    foot: nota del pie (HTML ya escapado), encima del contacto de la empresa.
    vos: el correo trata de "vos" (pedido web, cuentas); por defecto de "usted" (documentos y cobro)."""
    return _frame(brand(t), title=title, body=body, preheader=preheader, action=action, after_action=after_action, foot=foot, vos=vos, show_link=show_link)


def render_static(*, title: str, body: str, preheader: str | None = None, name: str = "Crimson Consulting", foot: str = "") -> str:
    """El mismo marco sin empresa ni base de datos (no carga la configuracion de la app): lo usa el aviso de falla
    del respaldo diario, que tiene que salir aunque la base o las variables sean lo que fallo. Colores por defecto,
    el nombre en el encabezado y info@crimsoncr.com en el pie."""
    b = Brand(name=name, primary=PRIMARY, logo=None, email=CONTACT_EMAIL, phone=None, whatsapp=None, address=None)
    return _frame(b, title=title, body=body, preheader=preheader, action=None, after_action="", foot=foot, vos=False, show_link=False)


def _frame(
    b: Brand,
    *,
    title: str,
    body: str,
    preheader: str | None,
    action: tuple[str, str] | None,
    after_action: str,
    foot: str,
    vos: bool,
    show_link: bool,
) -> str:
    if action and action[0]:
        boton = button(action[0], action[1], b.primary) + after_action + (fallback(action[0], vos) if show_link else "")
    else:
        boton = after_action
    pre = _preheader(preheader if preheader is not None else title)
    return f"""<div {MARKER}="1" style="margin:0;padding:0;background:{BG}">{pre}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{BG}" style="width:100%;background:{BG}">
<tr><td align="center" style="padding:24px 12px">
<!--[if mso]><table role="presentation" width="{WIDTH}" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" style="width:100%;max-width:{WIDTH}px;background:#ffffff;border:1px solid {LINE};border-radius:12px;font-family:{FONT};color:{INK}">
{_header(b)}
<tr><td style="padding:26px 28px 14px;font-family:{FONT};color:{INK}">
<h1 style="margin:0 0 14px;font-family:{FONT};font-size:22px;line-height:1.3;font-weight:700;color:{INK}">{e(title)}</h1>
{body}{boton}
</td></tr>
{_footer(b, foot)}
</table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr></table></div>"""


_LONE_LINK = re.compile(r"<p>\s*<a href=\"([^\"]+)\"[^>]*>([^<]{1,80})</a>\s*(?:\(([^<]{0,160})\))?\s*\.?\s*</p>", re.I)
_TAGS = (
    (re.compile(r"<p>", re.I), f'<p style="margin:0 0 12px;font-family:{FONT};font-size:15px;line-height:1.55;color:{INK}">'),
    (re.compile(r"<h3>", re.I), f'<h3 style="margin:16px 0 8px;font-family:{FONT};font-size:16px;line-height:1.3;color:{INK}">'),
    (re.compile(r"<ul>", re.I), f'<ul style="margin:0 0 14px;padding-left:20px;font-family:{FONT};font-size:14px;line-height:1.55;color:{INK}">'),
    (re.compile(r"<li>", re.I), '<li style="margin:0 0 4px">'),
    (
        re.compile(r"<blockquote>", re.I),
        f'<blockquote style="margin:4px 0 16px;padding:12px 16px;background:{SOFT};border-left:3px solid {LINE};'
        f'font-family:{FONT};font-size:14px;line-height:1.55;color:{INK};white-space:pre-wrap">',
    ),
    (re.compile(r"<table border=0 cellpadding=\d+>", re.I), f'<table role="presentation" width="100%" cellpadding="6" cellspacing="0" border="0" style="width:100%;margin:4px 0 16px;font-family:{FONT};font-size:13px;color:{INK}">'),
    (re.compile(r"<th(?=[ >])", re.I), f'<th style="font-family:{FONT};font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:{GREY};border-bottom:2px solid {LINE}"'),
    (re.compile(r"<td(?=[ >])(?![^>]*style=)", re.I), f'<td style="border-bottom:1px solid {LINE};font-family:{FONT};font-size:13px"'),
)


def _normalize(t: Tenant, fragment: str) -> tuple[str, tuple[str, str] | None]:
    """Un aviso armado a mano con <p>, <ul>, <blockquote> o <table> sale con los estilos del diseno base. Un parrafo que
    es SOLO un enlace (la llamada a la accion, p. ej. "<p><a href=...>Abrir</a></p>") pasa a ser el boton principal."""
    action = None
    m = _LONE_LINK.search(fragment)
    if m:
        action = (_html.unescape(m.group(1)), _html.unescape(m.group(2)).strip())
        resto = m.group(3)
        nota = p(f"({_html.unescape(resto)})", small=True) if resto else ""
        fragment = fragment[: m.start()] + nota + fragment[m.end() :]
    for rx, rep in _TAGS:
        fragment = rx.sub(rep, fragment)
    return fragment, action


def has_layout(html: str | None) -> bool:
    return MARKER in (html or "")


def ensure(t: Tenant, subject: str, html: str | None) -> str:
    """Red de seguridad de queue_email: un fragmento suelto sale dentro del marco con el asunto como titulo. No toca
    lo que ya tiene el marco ni un documento completo (<html>: el informe preliminar del levantamiento)."""
    html = html or ""
    if has_layout(html) or _FULL_DOC.match(html):
        return html
    titulo = (subject or "").split(" · ")[0].strip() or (subject or "")
    body, action = _normalize(t, html)
    return render(
        t, title=titulo, body=f'<div style="font-family:{FONT};font-size:15px;line-height:1.55;color:{INK}">{body}</div>', preheader=subject, action=action
    )
