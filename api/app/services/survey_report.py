"""Informe preliminar del levantamiento: lo que se le manda al cliente ANTES de cotizar.

Andres: "que del levantamiento se pueda realizar un informe preliminar con las anotaciones y las cosas que se ocupan".
Regla dura: NI UN monto. Ni costos, ni precios, ni tarifas de mano de obra: lo puede ver el cliente y todavia
no hay cotizacion. Por eso la plantilla no recibe el modelo crudo sino un dict armado aqui, solo con lo permitido
(el costo escrito en el costeo vive en SurveyItem.unit_cost y nunca se copia).

Usa el mismo motor que cotizaciones y facturas (services/render.py: Jinja + WeasyPrint si esta instalado).
Orden (Andres 13/10): notas y lo que se necesita primero; despues el detalle por punto con sus fotos.
"""

from __future__ import annotations

import re
from decimal import Decimal

from sqlalchemy.orm import Session

from ..models import Customer, Survey, Tenant, User
from .render import env
from .survey_specs import SPECS

# Llaves de un punto que jamas salen al cliente, por si algun tipo de levantamiento llegara a guardar plata ahi
_MONEY_KEY = re.compile(r"(cost|costo|precio|price|tarifa|margen|margin|monto|amount)", re.I)
LABOR_LABELS = {"tecnico": "Personal técnico", "civil": "Personal de obra civil", "contratado": "Personal contratado"}

# Fotos (macro `fotos`): Andres, 13/10 [01:00]: "todavia se pueden hacer un poquito mas grandes… es lo mas importante".
# Una sola foto va a todo el ancho (caja de 440 px de alto); varias van de dos en dos (cajas de 340 px), enteras y
# sin recorte sobre fondo claro. 340 px deja dos filas de fotos + el encabezado del punto en una hoja carta o A4
# (alto util ~925 px en carta con estos margenes). Es una tabla y no grid/flex porque el mismo HTML va en el cuerpo
# del correo, donde solo las tablas son fiables; en pantallas chicas de correo las fotos se apilan. Un punto con mas
# de 2 fotos (mas de una fila) puede partirse entre paginas, fila por fila, para no dejar media hoja en blanco.

TEMPLATE = env.from_string("""<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Informe preliminar {{ s.number }}</title>
<style>
@page { size: Letter; margin: 16mm 14mm 18mm; @bottom-center { content: "Documento preliminar, no constituye cotización · pág. " counter(page) " de " counter(pages); font-size: 9px; color: #8a858f; } }
body{font-family:Arial,Helvetica,sans-serif;color:#15131a;font-size:12px;line-height:1.45;margin:0}
.top{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:3px solid #e2233a;padding-bottom:14px;margin-bottom:16px}
.brand b{font-size:20px;letter-spacing:-.02em;display:block}.brand span{color:#5a5560;font-size:11px}
.docid{text-align:right}.k{font-family:Consolas,monospace;font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;color:#8a858f}
.docid .n{font-size:20px;font-weight:700;letter-spacing:-.02em}
.stamp{display:inline-block;margin-top:4px;padding:3px 9px;border-radius:999px;font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;background:#fde3e6;color:#e2233a}
.meta{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px}
.box{border:1px solid #e6e1db;border-radius:8px;padding:10px 12px}
h2{font-size:14px;margin:18px 0 8px;letter-spacing:-.01em;page-break-after:avoid}
.pt{border:1px solid #e6e1db;border-radius:8px;padding:10px 12px;margin-bottom:10px;page-break-inside:avoid}
.pt.largo{page-break-inside:auto}
.pt h3{margin:0 0 4px;font-size:13px}.pt .code{font-family:Consolas,monospace;color:#e2233a;margin-right:6px}
.kv{display:grid;grid-template-columns:repeat(3,1fr);gap:4px 14px;margin:6px 0}.kv div{font-size:11px}.kv b{font-weight:600}
.obs{background:#f6f2ee;border-radius:6px;padding:6px 9px;color:#5a5560;font-size:11px;white-space:pre-wrap;margin-top:6px}
table{width:100%;border-collapse:collapse}th{text-align:left;font-family:Consolas,monospace;font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;color:#8a858f;border-bottom:1px solid #e6e1db;padding:6px 4px}
td{padding:7px 4px;border-bottom:1px solid #f0ece7;vertical-align:top}.num{text-align:right;font-family:Consolas,monospace;white-space:nowrap}
.muted{color:#5a5560}.notes{padding:10px 12px;background:#f6f2ee;border-radius:8px;color:#3d3942;white-space:pre-wrap;border-left:3px solid #e2233a}
.foot{margin-top:22px;padding:10px 12px;border:1px dashed #e2233a;border-radius:8px;font-size:11px;color:#5a5560}
.ph1{margin-top:8px;height:440px;line-height:440px;text-align:center;background:#f6f2ee;border:1px solid #e6e1db;border-radius:8px;color:#5a5560;font-size:11px}
.ph1 img{max-width:100%;max-height:436px;vertical-align:middle}
table.ph{table-layout:fixed;margin-top:8px}
.ph td{padding:0 4px 8px 0;border:0;vertical-align:top}.ph td+td{padding:0 0 8px 4px}
.ph tr{page-break-inside:avoid}
.ph .f{height:340px;line-height:340px;text-align:center;background:#f6f2ee;border:1px solid #e6e1db;border-radius:8px;color:#5a5560;font-size:11px}
.ph .f img{max-width:100%;max-height:336px;vertical-align:middle}
.cap{font-family:Consolas,monospace;font-size:9px;letter-spacing:.08em;color:#8a858f;margin-top:3px}
@media screen and (max-width:520px){.ph td,.ph td+td{display:block;padding:0 0 8px}.ph1,.ph .f{height:auto;line-height:normal}.ph1 img,.ph .f img{max-height:420px}}
</style></head><body>
{% macro fotos(urls, ref) %}{% if urls|length == 1 %}<div class="ph1"><img src="{{ urls[0] }}" alt="Foto · {{ ref }}"></div><div class="cap">{{ ref|capitalize if ref == "general" else ref }} · foto 1</div>
{% else %}<table class="ph" role="presentation">{% for fila in urls|batch(2) %}{% set n = loop.index0 * 2 %}<tr>{% for u in fila %}<td><div class="f"><img src="{{ u }}" alt="Foto {{ n + loop.index }} · {{ ref }}"></div><div class="cap">{{ ref|capitalize if ref == "general" else ref }} · foto {{ n + loop.index }}</div></td>{% endfor %}</tr>{% endfor %}</table>
{% endif %}{% endmacro %}
<div class="top">
  <div class="brand">{% if logo %}<img src="{{ logo }}" alt="{{ t.name }}" style="display:block;height:42px;width:auto;max-width:220px;margin-bottom:8px">{% endif %}<b>{{ t.name }}</b><span>{{ t.legal_name or "" }}{% if t.tax_id %} · Cédula {{ t.tax_id }}{% endif %}</span></div>
  <div class="docid"><div class="k">Informe preliminar · {{ kind_label }}</div><div class="n">{{ s.number }}</div><span class="stamp">Preliminar</span></div>
</div>
<div class="meta">
  <div class="box"><div class="k">Cliente</div><b>{{ customer or "—" }}</b>{% if contact_line %}<br><span class="muted">{{ contact_line }}</span>{% endif %}
    <br><span class="k">Sitio</span><br>{{ s.site or "—" }}</div>
  <div class="box"><div class="k">Visita</div>Fecha: <b>{{ visit_date or "—" }}</b><br>Técnicos que visitaron: <b>{{ visitors|join(", ") if visitors else "—" }}</b>
    <br>{{ points|length }} {{ point_label|lower }}(s) · {{ equipos|length + materiales|length + servicios|length }} equipos, materiales y servicios</div>
</div>

{% if s.notes %}<h2>Notas del levantamiento</h2><div class="notes">{{ s.notes }}</div>{% endif %}

{% for title, rows in [("Equipos requeridos", equipos), ("Materiales requeridos", materiales), ("Servicios y alquileres", servicios)] %}{% if rows %}<h2>{{ title }}</h2>
<table><thead><tr><th>Descripción</th><th class="num">Cantidad</th><th>Observación</th></tr></thead><tbody>
{% for r in rows %}<tr><td><b>{{ r.name }}</b></td><td class="num">{{ r.quantity }} {{ r.unit }}</td><td class="muted">{{ r.note or "" }}</td></tr>{% endfor %}
</tbody></table>{% endif %}{% endfor %}

{% if labor %}<h2>Personal estimado para la obra</h2>
<table><thead><tr><th>Tipo</th><th class="num">Personas</th><th class="num">Días</th></tr></thead><tbody>
{% for l in labor %}<tr><td>{{ l.label }}</td><td class="num">{{ l.people }}</td><td class="num">{{ l.days }}</td></tr>{% endfor %}
</tbody></table>{% endif %}

{% if points %}<h2>Detalle por punto ({{ point_label|lower }})</h2>
{% for p in points %}<div class="pt{% if p.photos|length > 2 %} largo{% endif %}"><h3><span class="code">{{ p.code }}</span>{{ p.label or "" }}</h3>
  {% if p.data %}<div class="kv">{% for label, value in p.data %}<div><span class="muted">{{ label }}:</span> <b>{{ value }}</b></div>{% endfor %}</div>{% endif %}
  {% if p.notes %}<div class="obs">{{ p.notes }}</div>{% endif %}
  {% if p.photos %}{{ fotos(p.photos, p.code) }}{% endif %}
</div>{% endfor %}{% endif %}

{% if photos %}<h2>Fotos generales</h2>{{ fotos(photos, "general") }}{% endif %}

<div class="foot"><b>Documento preliminar, no constituye cotización.</b> Resume lo observado en la visita y lo que se estima
necesario para la obra; cantidades sujetas a confirmación. Las condiciones económicas se envían aparte, en la cotización formal.</div>
</body></html>""")


def _num(v) -> str:
    """3.000 -> "3", 2.50 -> "2.5": cantidades limpias, sin ceros de la columna Numeric."""
    n = Decimal(str(v or 0))
    return str(int(n)) if n == n.to_integral_value() else f"{n.normalize():f}"


def _photos(v) -> list[str]:
    """Solo URLs http(s) o rutas del propio sitio: nada de data: gigantes ni esquemas raros en el PDF."""
    out = []
    for x in v or []:
        u = x.get("url") if isinstance(x, dict) else x
        if isinstance(u, str) and (u.startswith("http://") or u.startswith("https://") or u.startswith("/")):
            out.append(u)
    return out[:12]


def _point_data(kind: str, data: dict) -> list[tuple[str, str]]:
    fields = {f["key"]: f for f in SPECS.get(kind, SPECS["otro"])["fields"]}
    out = []
    for k, v in (data or {}).items():
        if v in (None, "", [], False) or _MONEY_KEY.search(k):
            continue
        f = fields.get(k, {})
        if _MONEY_KEY.search(f.get("label", "")):
            continue
        if isinstance(v, bool):
            v = "Sí"
        elif isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        elif isinstance(v, (int, float, Decimal)):
            v = _num(v)
        unit, label = f.get("unit"), f.get("label") or k.replace("_", " ").capitalize()
        # la etiqueta a veces ya trae la unidad ("Distancia (m)"): no repetirla en el valor
        out.append((label, f"{v} {unit}" if unit and f"({unit})" not in label else str(v)))
    return out


def context(db: Session, s: Survey, tenant: Tenant, ttl: int | None = None, visitor_name=None) -> dict:
    """Todo lo que ve el cliente, y nada mas. La prueba de no-montos se hace sobre esto y sobre el HTML.
    Las fotos privadas salen con enlace firmado (ttl en segundos; None = el corto del portal). visitor_name(uid,
    nombre) -> nombre a mostrar de cada tecnico de la visita (el portal de aliados: los de Crimson, solo el de pila)."""
    from .media_access import sign_urls

    def fotos(v) -> list[str]:
        return sign_urls(db, tenant.id, _photos(v), ttl)

    from ..routers.fieldwork import survey_labor  # misma lectura de personal que el portal

    c = db.get(Customer, s.customer_id) if s.customer_id else None
    contact = s.contact or {}
    ids = [int(x) for x in (s.visit_tech_ids or []) if str(x).isdigit()] or ([s.technician_id] if s.technician_id else [])
    visitors = [n for n in ((visitor_name(u.id, u.full_name) if visitor_name else u.full_name) for u in (db.get(User, i) for i in ids) if u) if n]
    spec = SPECS.get(s.kind, SPECS["otro"])
    # puntos de otro tipo (una puerta en un levantamiento de CCTV): cada uno con sus campos y su nombre
    mixed = any(p.kind and p.kind != s.kind for p in s.points)
    items = [{"name": i.name, "quantity": _num(i.quantity), "unit": i.unit, "note": i.note, "kind": i.kind or "material"} for i in s.items]
    labor = [
        {"label": LABOR_LABELS.get(k, k), "people": v["people"], "days": _num(v["days"])}
        for k, v in survey_labor(s).items()
        if v["people"] and Decimal(str(v["days"] or 0)) > 0
    ]
    return {
        "s": {"number": s.number, "site": s.site, "notes": s.notes},
        "t": tenant,
        "kind_label": spec["label"],
        "point_label": "Punto" if mixed else spec["point_label"],
        "customer": c.name if c else contact.get("name"),
        "contact_line": " · ".join(x for x in (contact.get("name") if c else None, contact.get("phone"), contact.get("email")) if x),
        "visit_date": s.visit_date.strftime("%d/%m/%Y") if s.visit_date else None,
        "visitors": visitors,
        "points": [
            {
                "code": p.code,
                "label": " · ".join(x for x in (SPECS[p.kind]["point_label"] if mixed and p.kind in SPECS else None, p.label) if x),
                "data": _point_data(p.kind or s.kind, p.data),
                "notes": p.notes,
                "photos": fotos(p.photos),
            }
            for p in s.points
        ],
        "equipos": [i for i in items if i["kind"] == "equipo"],
        "materiales": [i for i in items if i["kind"] not in ("equipo", "servicio")],
        "servicios": [i for i in items if i["kind"] == "servicio"],
        "labor": labor,
        "photos": fotos(s.photos),
    }


def render_survey_html(db: Session, s: Survey, tenant: Tenant, ttl: int | None = None, visitor_name=None) -> str:
    """ttl: vida de los enlaces de fotos privadas. El PDF se arma al instante (corto); el correo, largo."""
    from .render import brand_ctx

    # el mismo logo que la cotizacion y la factura (incrustado; antes era t.logo_url, un dominio viejo de Railway)
    return TEMPLATE.render(**context(db, s, tenant, ttl, visitor_name), logo=brand_ctx(tenant)["logo"])


def report_email_html(tenant: Tenant, number: str, customer: str | None, site: str | None, message: str | None, *, attached_pdf: bool) -> str:
    """El correo del informe preliminar al cliente (diseno base: email_layout). El informe va ADJUNTO (PDF, o el
    imprimible .html si el servidor no arma PDF): la pagina del informe trae <style> y clases que Outlook y Hotmail
    descartan, asi que no va como cuerpo. El mensaje de quien envia va como texto, nunca como HTML."""
    import html as _h
    from datetime import datetime as _dt

    from . import email_layout as el
    from .documents import CR

    titulo = f"Informe preliminar {number}"
    cuerpo = (
        el.p_html(f"Le compartimos el <b>informe preliminar {_h.escape(number)}</b> del levantamiento técnico, antes de la cotización.")
        + el.data_rows([("Informe", number), ("Cliente", customer), ("Sitio", site), ("Fecha", _dt.now(CR).strftime("%d/%m/%Y"))])
        + (el.note(message) if message else "")
        + el.p("El informe va adjunto en PDF." if attached_pdf else "El informe va adjunto: ábralo en el navegador para verlo o imprimirlo.", small=True)
    )
    return el.render(
        tenant,
        title=titulo,
        body=cuerpo,
        preheader=titulo + (f" · {site}" if site else "") + ". El informe va adjunto.",
        foot="Cualquier consulta, responda a este correo.",
    )
