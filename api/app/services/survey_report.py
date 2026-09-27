"""Informe preliminar del levantamiento: lo que se le manda al cliente ANTES de cotizar.

Andres: "que del levantamiento se pueda realizar un informe preliminar con las anotaciones y las cosas que se ocupan".
Regla dura: NI UN monto. Ni costos, ni precios, ni tarifas de mano de obra: lo puede ver el cliente y todavia
no hay cotizacion. Por eso la plantilla no recibe el modelo crudo sino un dict armado aqui, solo con lo permitido
(el costo escrito en el costeo vive en SurveyItem.unit_cost y nunca se copia).

Usa el mismo motor que cotizaciones y facturas (services/render.py: Jinja + WeasyPrint si esta instalado).
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

TEMPLATE = env.from_string("""<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Informe preliminar {{ s.number }}</title>
<style>
@page { size: Letter; margin: 16mm 14mm 18mm; @bottom-center { content: "Documento preliminar, no constituye cotización · pág. " counter(page) " de " counter(pages); font-size: 9px; color: #8a858f; } }
body{font-family:Manrope,"Segoe UI",Arial,sans-serif;color:#15131a;font-size:12px;line-height:1.45;margin:0}
.top{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:3px solid #e2233a;padding-bottom:14px;margin-bottom:16px}
.brand b{font-size:20px;letter-spacing:-.02em;display:block}.brand span{color:#5a5560;font-size:11px}
.docid{text-align:right}.k{font-family:Consolas,monospace;font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;color:#8a858f}
.docid .n{font-size:20px;font-weight:700;letter-spacing:-.02em}
.stamp{display:inline-block;margin-top:4px;padding:3px 9px;border-radius:999px;font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;background:#fde3e6;color:#e2233a}
.meta{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px}
.box{border:1px solid #e6e1db;border-radius:8px;padding:10px 12px}
h2{font-size:14px;margin:18px 0 8px;letter-spacing:-.01em}
.pt{border:1px solid #e6e1db;border-radius:8px;padding:10px 12px;margin-bottom:10px;page-break-inside:avoid}
.pt h3{margin:0 0 4px;font-size:13px}.pt .code{font-family:Consolas,monospace;color:#e2233a;margin-right:6px}
.kv{display:grid;grid-template-columns:repeat(3,1fr);gap:4px 14px;margin:6px 0}.kv div{font-size:11px}.kv b{font-weight:600}
.obs{background:#f6f2ee;border-radius:6px;padding:6px 9px;color:#5a5560;font-size:11px;white-space:pre-wrap;margin-top:6px}
.ph{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}.ph img{width:110px;height:82px;object-fit:cover;border-radius:6px;border:1px solid #e6e1db}
table{width:100%;border-collapse:collapse}th{text-align:left;font-family:Consolas,monospace;font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;color:#8a858f;border-bottom:1px solid #e6e1db;padding:6px 4px}
td{padding:7px 4px;border-bottom:1px solid #f0ece7;vertical-align:top}.num{text-align:right;font-family:Consolas,monospace;white-space:nowrap}
.muted{color:#5a5560}.notes{padding:10px 12px;background:#f6f2ee;border-radius:8px;color:#3d3942;white-space:pre-wrap}
.foot{margin-top:22px;padding:10px 12px;border:1px dashed #e2233a;border-radius:8px;font-size:11px;color:#5a5560}
</style></head><body>
<div class="top">
  <div class="brand">{% if t.logo_url %}<img src="{{ t.logo_url }}" style="height:34px;margin-bottom:6px">{% endif %}<b>{{ t.name }}</b><span>{{ t.legal_name or "" }}{% if t.tax_id %} · Cédula {{ t.tax_id }}{% endif %}</span></div>
  <div class="docid"><div class="k">Informe preliminar · {{ kind_label }}</div><div class="n">{{ s.number }}</div><span class="stamp">Preliminar</span></div>
</div>
<div class="meta">
  <div class="box"><div class="k">Cliente</div><b>{{ customer or "—" }}</b>{% if contact_line %}<br><span class="muted">{{ contact_line }}</span>{% endif %}
    <br><span class="k">Sitio</span><br>{{ s.site or "—" }}</div>
  <div class="box"><div class="k">Visita</div>Fecha: <b>{{ visit_date or "—" }}</b><br>Técnicos que visitaron: <b>{{ visitors|join(", ") if visitors else "—" }}</b>
    <br>{{ points|length }} {{ point_label|lower }}(s) · {{ equipos|length + materiales|length }} equipos y materiales</div>
</div>

{% if points %}<h2>{{ point_label }}s levantados</h2>
{% for p in points %}<div class="pt"><h3><span class="code">{{ p.code }}</span>{{ p.label or "" }}</h3>
  {% if p.data %}<div class="kv">{% for label, value in p.data %}<div><span class="muted">{{ label }}:</span> <b>{{ value }}</b></div>{% endfor %}</div>{% endif %}
  {% if p.notes %}<div class="obs">{{ p.notes }}</div>{% endif %}
  {% if p.photos %}<div class="ph">{% for u in p.photos %}<img src="{{ u }}" alt="">{% endfor %}</div>{% endif %}
</div>{% endfor %}{% endif %}

{% for title, rows in [("Equipos requeridos", equipos), ("Materiales requeridos", materiales)] %}{% if rows %}<h2>{{ title }}</h2>
<table><thead><tr><th>Descripción</th><th class="num">Cantidad</th><th>Observación</th></tr></thead><tbody>
{% for r in rows %}<tr><td><b>{{ r.name }}</b></td><td class="num">{{ r.quantity }} {{ r.unit }}</td><td class="muted">{{ r.note or "" }}</td></tr>{% endfor %}
</tbody></table>{% endif %}{% endfor %}

{% if labor %}<h2>Personal estimado para la obra</h2>
<table><thead><tr><th>Tipo</th><th class="num">Personas</th><th class="num">Días</th></tr></thead><tbody>
{% for l in labor %}<tr><td>{{ l.label }}</td><td class="num">{{ l.people }}</td><td class="num">{{ l.days }}</td></tr>{% endfor %}
</tbody></table>{% endif %}

{% if s.notes %}<h2>Notas del levantamiento</h2><div class="notes">{{ s.notes }}</div>{% endif %}
{% if photos %}<h2>Fotos generales</h2><div class="ph">{% for u in photos %}<img src="{{ u }}" alt="">{% endfor %}</div>{% endif %}

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
        unit = f.get("unit")
        out.append((f.get("label") or k.replace("_", " ").capitalize(), f"{v} {unit}" if unit else str(v)))
    return out


def context(db: Session, s: Survey, tenant: Tenant) -> dict:
    """Todo lo que ve el cliente, y nada mas. La prueba de no-montos se hace sobre esto y sobre el HTML."""
    from ..routers.fieldwork import survey_labor  # misma lectura de personal que el portal

    c = db.get(Customer, s.customer_id) if s.customer_id else None
    contact = s.contact or {}
    ids = [int(x) for x in (s.visit_tech_ids or []) if str(x).isdigit()] or ([s.technician_id] if s.technician_id else [])
    visitors = [u.full_name for u in (db.get(User, i) for i in ids) if u]
    spec = SPECS.get(s.kind, SPECS["otro"])
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
        "point_label": spec["point_label"],
        "customer": c.name if c else contact.get("name"),
        "contact_line": " · ".join(x for x in (contact.get("name") if c else None, contact.get("phone"), contact.get("email")) if x),
        "visit_date": s.visit_date.strftime("%d/%m/%Y") if s.visit_date else None,
        "visitors": visitors,
        "points": [{"code": p.code, "label": p.label, "data": _point_data(s.kind, p.data), "notes": p.notes, "photos": _photos(p.photos)} for p in s.points],
        "equipos": [i for i in items if i["kind"] == "equipo"],
        "materiales": [i for i in items if i["kind"] != "equipo"],
        "labor": labor,
        "photos": _photos(s.photos),
    }


def render_survey_html(db: Session, s: Survey, tenant: Tenant) -> str:
    return TEMPLATE.render(**context(db, s, tenant))
