"""Cotizacion/factura en HTML (plantilla propia) y PDF (WeasyPrint si esta instalado; en Docker si)."""

from __future__ import annotations

from decimal import Decimal

from jinja2 import Environment, select_autoescape
from sqlalchemy.orm import Session

from ..models import Customer, Invoice, Quote, Tenant

env = Environment(autoescape=select_autoescape(["html"]))


def money(v, cur="CRC") -> str:
    n = Decimal(str(v or 0)).quantize(Decimal("0.01"))
    s = f"{n:,.2f}"
    return ("₡" if cur == "CRC" else "$") + s


env.filters["money"] = money

TEMPLATE = env.from_string("""<!doctype html><html lang="es"><head><meta charset="utf-8"><title>{{ kind }} {{ d.number }}</title>
<style>
@page { size: Letter; margin: 18mm 16mm; }
body{font-family:Manrope,"Segoe UI",Arial,sans-serif;color:#15131a;font-size:12px;line-height:1.45;margin:0}
.top{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:3px solid #e2233a;padding-bottom:14px;margin-bottom:18px}
.brand b{font-size:20px;letter-spacing:-.02em;display:block}.brand span{color:#5a5560;font-size:11px}
.docid{text-align:right}.docid .k{font-family:Consolas,monospace;font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:#8a858f}
.docid .n{font-size:22px;font-weight:700;letter-spacing:-.02em}
.meta{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:18px}
.box{border:1px solid #e6e1db;border-radius:8px;padding:10px 12px}.box .k{font-family:Consolas,monospace;font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;color:#8a858f;margin-bottom:4px}
table{width:100%;border-collapse:collapse;margin-top:6px}th{text-align:left;font-family:Consolas,monospace;font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;color:#8a858f;border-bottom:1px solid #e6e1db;padding:6px 4px}
td{padding:8px 4px;border-bottom:1px solid #f0ece7;vertical-align:top}.num{text-align:right;font-family:Consolas,monospace;white-space:nowrap}
.desc{color:#5a5560;font-size:11px}.tot{width:46%;margin-left:auto;margin-top:12px}.tot td{padding:5px 4px;border:0}.tot .grand td{border-top:2px solid #15131a;font-size:15px;font-weight:700;padding-top:8px}
.notes{margin-top:18px;padding:10px 12px;background:#f6f2ee;border-radius:8px;color:#5a5560;font-size:11px;white-space:pre-wrap}
.foot{margin-top:24px;padding-top:10px;border-top:1px solid #e6e1db;font-size:10px;color:#8a858f;display:flex;justify-content:space-between}
.stamp{display:inline-block;padding:3px 9px;border-radius:999px;font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;background:#f6f2ee;color:#5a5560}
.stamp.paid{background:#e4f6ea;color:#1f9d55}.stamp.void{background:#fde3e6;color:#e2233a}
</style></head><body>
<div class="top">
  <div class="brand">{% if t.logo_url %}<img src="{{ t.logo_url }}" style="height:34px;margin-bottom:6px">{% endif %}<b>{{ t.name }}</b><span>{{ t.legal_name or "" }}{% if t.tax_id %} · Cédula {{ t.tax_id }}{% endif %}</span></div>
  <div class="docid"><div class="k">{{ kind }}</div><div class="n">{{ d.number }}</div>
    {% if d.consecutive %}<div class="k">Consecutivo {{ d.consecutive }}</div>{% endif %}
    {% if d.clave %}<div class="k" style="text-transform:none">Clave {{ d.clave }}</div>{% endif %}
    <span class="stamp {% if d.status == 'pagada' %}paid{% elif d.status == 'anulada' %}void{% endif %}">{{ d.status }}</span></div>
</div>
<div class="meta">
  <div class="box"><div class="k">Cliente</div><b>{{ c.name if c else "—" }}</b><br>{% if c and c.id_number %}{{ c.id_type }} {{ c.id_number }}<br>{% endif %}{% if c and c.email %}{{ c.email }}<br>{% endif %}{% if c and c.phone %}{{ c.phone }}{% endif %}</div>
  <div class="box"><div class="k">Detalle</div>Fecha: <b>{{ d.issue_date }}</b><br>{% if d.due_date %}Vence: <b>{{ d.due_date }}</b><br>{% endif %}Divisa: <b>{{ d.currency }}</b>{% if d.currency != 'CRC' %} · TC {{ d.fx_sell }}{% endif %}{% if d.external_order %}<br>Orden externa: {{ d.external_order }}{% endif %}{% if d.activity_code %}<br>Actividad: {{ d.activity_code }}{% endif %}</div>
</div>
<table><thead><tr><th>Concepto</th><th class="num">Cant.</th><th class="num">Precio</th><th class="num">Desc.</th><th class="num">IVA</th><th class="num">Subtotal</th></tr></thead><tbody>
{% for l in d.lines %}<tr><td><b>{{ l.name }}</b>{% if l.description %}<div class="desc">{{ l.description }}</div>{% endif %}{% if l.cabys_code %}<div class="desc">CABYS {{ l.cabys_code }}</div>{% endif %}</td>
<td class="num">{{ "%g"|format(l.quantity|float) }} {{ l.unit }}</td><td class="num">{{ l.unit_price|money(d.currency) }}</td><td class="num">{% if l.discount_value|float > 0 %}{{ "%g"|format(l.discount_value|float) }}{{ "%" if l.discount_type == "percent" else "" }}{% else %}—{% endif %}</td><td class="num">{{ "%g"|format(l.tax_rate|float) }}%</td><td class="num">{{ l.subtotal|money(d.currency) }}</td></tr>{% endfor %}
</tbody></table>
<table class="tot"><tr><td>Subtotal</td><td class="num">{{ d.subtotal|money(d.currency) }}</td></tr>
{% if d.discount_total|float > 0 %}<tr><td>Descuento</td><td class="num">−{{ d.discount_total|money(d.currency) }}</td></tr>{% endif %}
<tr><td>Impuestos</td><td class="num">{{ d.tax_total|money(d.currency) }}</td></tr>
<tr class="grand"><td>Total</td><td class="num">{{ d.total|money(d.currency) }}</td></tr>
{% if balance is not none %}<tr><td>Saldo</td><td class="num">{{ balance|money(d.currency) }}</td></tr>{% endif %}</table>
{% if d.external_notes %}<div class="notes">{{ d.external_notes }}</div>{% endif %}
{% if footer %}<div class="notes">{{ footer }}</div>{% endif %}
<div class="foot"><span>{{ t.name }}{% if t.tax_id %} · {{ t.tax_id }}{% endif %}</span><span>Generado por Crimson · plataforma de facturación</span></div>
</body></html>""")


def render_html(db: Session, doc: Quote | Invoice, tenant: Tenant) -> str:
    c = db.get(Customer, doc.customer_id) if doc.customer_id else None
    is_inv = isinstance(doc, Invoice)
    st = tenant.settings or {}
    return TEMPLATE.render(
        d=doc,
        t=tenant,
        c=c,
        kind="Factura" if is_inv else "Cotización",
        balance=doc.balance if is_inv else None,
        footer=st.get("invoice_footer" if is_inv else "quote_footer"),
    )


def render_pdf(html: str) -> bytes | None:
    """Devuelve None si WeasyPrint no esta disponible (Windows sin GTK); la API entonces sirve HTML imprimible."""
    try:
        from weasyprint import HTML  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    return HTML(string=html).write_pdf()
