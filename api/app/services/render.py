"""Cotizacion/factura en HTML (plantilla propia) y PDF (WeasyPrint si esta instalado; en Docker si)."""

from __future__ import annotations

from decimal import Decimal

from jinja2 import Environment, select_autoescape
from sqlalchemy.orm import Session

from ..models import Customer, Invoice, Quote, Tenant
from .units import LEYENDA, unit_label

env = Environment(autoescape=select_autoescape(["html"]))


def money(v, cur="CRC") -> str:
    n = Decimal(str(v or 0)).quantize(Decimal("0.01"))
    s = f"{n:,.2f}"
    return ("₡" if cur == "CRC" else "$") + s


env.filters["money"] = money
env.filters["unidad"] = unit_label


def parrafos(texto) -> list[str]:
    """Clausulas y notas en parrafos: las lineas en blanco repetidas (que en el PDF eran media pagina de aire) se
    vuelven un solo corte. No recorta texto: todo lo escrito sale."""
    import re

    t = str(texto or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]


env.filters["parrafos"] = parrafos

# 30/10: diseno de la representacion grafica (PDF) de cotizacion y factura. Solo cambia la presentacion: todos los datos
# fiscales siguen saliendo igual (clave, consecutivo, cedula, actividad, CABYS por linea, impuestos, franja de simulado o
# pruebas y la leyenda legal de Ajustes). El XML que va a Hacienda no pasa por aqui.
TEMPLATE = env.from_string("""<!doctype html><html lang="es"><head><meta charset="utf-8"><title>{{ kind }} {{ d.number }}</title>
<style>
@page { size: Letter; margin: 15mm 15mm 18mm; @bottom-right { content: "Página " counter(page) " de " counter(pages); font-family: Arial, Helvetica, sans-serif; font-size: 8px; color: #8a858f; } }
body{font-family:Arial,Helvetica,sans-serif;color:#15131a;font-size:11.5px;line-height:1.45;margin:0}
.accent{height:5px;border-radius:3px;background:{{ primary }};margin-bottom:16px}
.top{display:flex;justify-content:space-between;align-items:flex-start;gap:18px;padding-bottom:16px;margin-bottom:16px;border-bottom:1px solid #e6e1db}
.brand{max-width:58%}.brand img{display:block;height:46px;width:auto;max-width:230px;margin-bottom:10px}
.brand b{font-size:18px;letter-spacing:-.01em;display:block}.brand .co{display:block;color:#5a5560;font-size:10.5px;line-height:1.5}
.docid{text-align:right;max-width:42%}
.docid .kind{font-size:10px;letter-spacing:.16em;text-transform:uppercase;font-weight:700;color:{{ primary }}}
.docid .n{font-size:24px;font-weight:700;letter-spacing:-.02em;margin:2px 0 6px}
.docid .k{font-size:9.5px;letter-spacing:.06em;text-transform:uppercase;color:#8a858f;margin-top:3px;overflow-wrap:anywhere}
.lbl{font-size:9px;letter-spacing:.14em;text-transform:uppercase;font-weight:700;color:#8a858f;margin-bottom:5px}
.meta{display:flex;gap:12px;margin-bottom:16px}
.box{flex:1;border:1px solid #e6e1db;border-radius:10px;padding:11px 13px;background:#fcfbfa}
.box b{font-size:12.5px}
table{width:100%;border-collapse:collapse;margin-top:4px}
.lines thead th{text-align:left;font-size:9px;letter-spacing:.12em;text-transform:uppercase;font-weight:700;color:#ffffff;background:#15131a;padding:8px 7px}
.lines thead th:first-child{border-radius:8px 0 0 8px}.lines thead th:last-child{border-radius:0 8px 8px 0}
.lines tbody td{padding:9px 7px;border-bottom:1px solid #f0ece7;vertical-align:top}
.lines tbody tr:nth-child(even) td{background:#faf8f6}
.num,.lines thead th.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.desc{color:#5a5560;font-size:10.5px;margin-top:2px}.cabys{color:#8a858f;font-size:9.5px;margin-top:2px;letter-spacing:.02em}
.sumrow{display:flex;justify-content:flex-end;margin-top:14px}
.totbox{width:46%;background:#f6f2ee;border-radius:10px;padding:6px 14px}
.tot{margin:0}.tot td{padding:5px 2px;border:0}.tot .grand td{border-top:2px solid #15131a;font-size:15px;font-weight:700;padding-top:9px}
.tot .grand td.num{color:{{ primary }}}
.notes{margin-top:18px;padding:11px 13px;background:#f6f2ee;border-radius:10px;color:#5a5560;font-size:11px;white-space:pre-wrap}
.notes--compact{margin-top:14px;padding:8px 10px;font-size:10px;line-height:1.35;white-space:normal}
.notes--compact p{margin:0 0 4px;white-space:pre-line;overflow-wrap:anywhere}.notes--compact p:last-child{margin-bottom:0}
.legal{margin-top:10px;padding:6px 9px;border:1px solid #ece7e1;border-radius:6px;color:#6b6670;font-size:8px;line-height:1.3}
.legal p{margin:0 0 3px;white-space:pre-line;overflow-wrap:anywhere}.legal p:last-child{margin-bottom:0}
.foot{margin-top:22px;padding-top:10px;border-top:1px solid #e6e1db;font-size:9.5px;color:#8a858f;display:flex;justify-content:space-between;gap:12px}
.stamp{display:inline-block;padding:3px 10px;border-radius:999px;font-size:9.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;background:#f6f2ee;color:#5a5560}
.tag{display:inline-block;margin-left:6px;padding:1px 7px;border-radius:999px;font-size:9px;font-weight:700;letter-spacing:.04em;background:#f6f2ee;color:#5a5560;vertical-align:1px}
.excl{margin-top:14px;border:1px dashed #d9d2ca;border-radius:10px;padding:10px 12px;font-size:11px;color:#5a5560}.excl ul{margin:0;padding-left:18px}
.stamp.paid{background:#e4f6ea;color:#1f9d55}.stamp.void{background:#fde3e6;color:#e2233a}
.band{margin:0 0 14px;padding:8px 12px;border-radius:8px;background:#fff4d6;color:#7a5a00;font-weight:700;font-size:11px;letter-spacing:.04em;text-transform:uppercase;text-align:center}
@media screen{body{max-width:816px;margin:24px auto;padding:0 24px}}
@media screen and (max-width:640px){body{margin:12px auto;padding:0 14px}.meta,.top{flex-direction:column}.brand,.docid{max-width:none}.totbox{width:100%}.docid{text-align:left}th,td{padding:6px 3px}}
</style></head><body>
<div class="accent"></div>
{% if band %}<div class="band">{{ band }}</div>{% endif %}
<div class="top">
  <div class="brand">{% if logo %}<img src="{{ logo }}" alt="{{ t.name }}">{% endif %}<b>{{ t.legal_name or t.name }}</b>
    <span class="co">{% if t.legal_name and t.legal_name != t.name %}{{ t.name }}{% if t.tax_id %} · {% endif %}{% endif %}{% if t.tax_id %}Cédula {{ t.tax_id }}{% endif %}</span>
    {% if b %}<span class="co">{{ b.email }}{% if b.phone %} · Tel. {{ b.phone }}{% endif %}</span>{% if b.address %}<span class="co">{{ b.address }}</span>{% endif %}{% endif %}</div>
  <div class="docid"><div class="kind">{{ kind }}</div><div class="n">{{ d.number }}</div>
    <span class="stamp {% if d.status == 'pagada' %}paid{% elif d.status == 'anulada' %}void{% endif %}">{{ d.status }}</span>
    {% if d.consecutive %}<div class="k">Consecutivo {{ d.consecutive }}</div>{% endif %}
    {% if d.clave %}<div class="k" style="text-transform:none">{{ clave_label }} {{ d.clave }}</div>{% endif %}</div>
</div>
<div class="meta">
  <div class="box"><div class="lbl">Cliente</div><b>{{ c.name if c else "—" }}</b><br>{% if c and c.id_number %}{{ c.id_type }} {{ c.id_number }}<br>{% endif %}{% if c and c.email %}{{ c.email }}<br>{% endif %}{% if c and c.phone %}{{ c.phone }}{% endif %}</div>
  <div class="box"><div class="lbl">Detalle</div>Fecha: <b>{{ d.issue_date }}</b><br>{% if d.due_date %}Vence: <b>{{ d.due_date }}</b><br>{% endif %}Divisa: <b>{{ d.currency }}</b>{% if d.currency != 'CRC' %} · TC {{ d.fx_sell }}{% endif %}{% if d.external_order %}<br>Orden externa: {{ d.external_order }}{% endif %}{% if d.activity_code %}<br>Actividad: {{ d.activity_code }}{% endif %}</div>
</div>
<table class="lines"><thead><tr><th>Concepto</th><th class="num">Cant.</th><th class="num">Precio</th><th class="num">Desc.</th><th class="num">IVA</th><th class="num">Subtotal</th></tr></thead><tbody>
{% for l in d.lines if (l.treatment or "normal") != "excluido" %}{% set nosuma = l.treatment in ("aportado", "cortesia") %}<tr><td><b>{{ l.name }}</b>{% if nosuma %} <span class="tag">{% if l.treatment == "aportado" and l.supplied_by %}Aportado por {{ l.supplied_by }}{% else %}{{ leyenda[l.treatment] }}{% endif %}</span>{% endif %}{% if l.description %}<div class="desc">{{ l.description }}</div>{% endif %}{% if l.cabys_code %}<div class="cabys">CABYS {{ l.cabys_code }}</div>{% endif %}</td>
<td class="num">{{ "%g"|format(l.quantity|float) }} {{ l.unit|unidad }}</td>{% if nosuma %}<td class="num" colspan="3">—</td><td class="num">No suma</td>{% else %}<td class="num">{{ l.unit_price|money(d.currency) }}</td><td class="num">{% if l.discount_value|float > 0 %}{{ "%g"|format(l.discount_value|float) }}{{ "%" if l.discount_type == "percent" else "" }}{% else %}—{% endif %}</td><td class="num">{{ "%g"|format(l.tax_rate|float) }}%</td><td class="num">{{ l.subtotal|money(d.currency) }}</td>{% endif %}</tr>{% endfor %}
</tbody></table>
{% set excl = d.lines|selectattr("treatment", "equalto", "excluido")|list %}{% if excl %}<div class="excl"><div class="lbl">Exclusiones · no incluidas en esta oferta</div><ul>{% for l in excl %}<li><b>{{ l.name }}</b>{% if l.quantity|float > 0 %} · {{ "%g"|format(l.quantity|float) }} {{ l.unit|unidad }}{% endif %}{% if l.description %} — {{ l.description }}{% endif %}</li>{% endfor %}</ul></div>{% endif %}
<div class="sumrow"><div class="totbox"><table class="tot"><tr><td>Subtotal</td><td class="num">{{ d.subtotal|money(d.currency) }}</td></tr>
{% if d.discount_total|float > 0 %}<tr><td>Descuento</td><td class="num">−{{ d.discount_total|money(d.currency) }}</td></tr>{% endif %}
<tr><td>Impuestos</td><td class="num">{{ d.tax_total|money(d.currency) }}</td></tr>
<tr class="grand"><td>Total</td><td class="num">{{ d.total|money(d.currency) }}</td></tr>
{% if balance is not none %}<tr><td>Saldo</td><td class="num">{{ balance|money(d.currency) }}</td></tr>{% endif %}</table></div></div>
{# Pedido 21/10 (item 17): en la FACTURA las notas y las clausulas van compactas (parrafos sin lineas en blanco
   repetidas, letra menor); la cotizacion conserva su tamano: sus condiciones son las que el cliente lee para aceptar #}
{% if d.external_notes %}{% if compact %}<div class="notes notes--compact">{% for par in d.external_notes|parrafos %}<p>{{ par }}</p>{% endfor %}</div>{% else %}<div class="notes">{{ d.external_notes }}</div>{% endif %}{% endif %}
{% if footer %}{% if compact %}<div class="legal">{% for par in footer|parrafos %}<p>{{ par }}</p>{% endfor %}</div>{% else %}<div class="notes">{{ footer }}</div>{% endif %}{% endif %}
<div class="foot"><span>{{ t.name }}{% if t.tax_id %} · {{ t.tax_id }}{% endif %}{% if b %} · {{ b.email }}{% endif %}</span><span>Generado por Crimson · plataforma de facturación</span></div>
</body></html>""")


def brand_ctx(tenant: Tenant) -> dict:
    """Logo, color y contacto de la empresa para los documentos (PDF). El logo es el mismo de los correos (Mi Tienda o
    el de la empresa) y va incrustado (data:) cuando se pudo bajar: WeasyPrint no sale a la red en cada PDF y un
    dominio viejo o caido no deja el documento sin logo."""
    import base64

    from . import email_layout as el

    b = el.brand(tenant)
    logo = b.logo or (tenant.logo_url or None)
    data = el.logo_bytes(logo)
    if data:
        tipo = {b"\x89P": "png", b"GI": "gif", b"\xff\xd8": "jpeg"}.get(data[:2], "png")
        logo = f"data:image/{tipo};base64,{base64.b64encode(data).decode()}"
    return {"logo": logo, "primary": b.primary, "b": b}


AVISO_DE_COBRO = "aviso de cobro"


def doc_kind(doc: Quote | Invoice) -> str:
    """Como se le presenta el documento al cliente, en minuscula ("factura", "cotización" o "aviso de cobro").

    Plan 1.4: lo que genera una recurrencia (la mensualidad de un aliado, el mantenimiento o el monitoreo mensual a un
    cliente) sin comprobante fiscal aceptado (modo de Hacienda "ninguno" o sin emitir) es un "aviso de cobro", no una
    factura: contabilidad emite el comprobante real aparte y el mismo cobro no circula como dos facturas. Una sola regla
    para el primer correo, los recordatorios de la secuencia, el PDF que descarga el aliado y la impresion: el mismo
    documento nunca sale con dos titulos."""
    if not isinstance(doc, Invoice):
        return "cotización"
    from .ally_access import PLAN_ORDER

    if (doc.external_order == PLAN_ORDER or doc.recurrence_id is not None) and doc.einvoice_status != "aceptada":
        return AVISO_DE_COBRO
    return "factura"


def doc_title(doc: Quote | Invoice) -> str:
    k = doc_kind(doc)
    return f"{k[:1].upper()}{k[1:]}"


def render_html(db: Session, doc: Quote | Invoice, tenant: Tenant, kind: str | None = None) -> str:
    """kind cambia el titulo; por defecto doc_title ("Aviso de cobro" en la mensualidad sin comprobante fiscal)."""
    c = db.get(Customer, doc.customer_id) if doc.customer_id else None
    is_inv = isinstance(doc, Invoice)
    st = tenant.settings or {}
    band, clave_label = fiscal_band(db, doc) if is_inv else (None, "Clave")
    return TEMPLATE.render(
        d=doc,
        t=tenant,
        c=c,
        kind=kind or doc_title(doc),
        leyenda=LEYENDA,
        balance=doc.balance if is_inv else None,
        footer=st.get("invoice_footer" if is_inv else "quote_footer"),
        compact=is_inv,
        band=band,
        clave_label=clave_label,
        **brand_ctx(tenant),
    )


def fiscal_band(db: Session, inv: Invoice) -> tuple[str | None, str]:
    """Franja del PDF segun el ultimo comprobante aceptado: simulado y pruebas no tienen validez fiscal."""
    from sqlalchemy import select

    from ..models import EInvoiceDocument

    doc = db.scalar(
        select(EInvoiceDocument)
        .where(EInvoiceDocument.invoice_id == inv.id, EInvoiceDocument.doc_type == inv.doc_type)
        .order_by(EInvoiceDocument.id.desc())
    )
    if doc is None:
        return None, "Clave"
    if doc.mode == "simulado" or doc.provider == "sandbox":
        return "Comprobante simulado · sin validez fiscal", "Clave (simulada)"
    if doc.mode == "pruebas":
        return "Ambiente de pruebas de Hacienda · sin validez fiscal", "Clave (pruebas)"
    return None, "Clave"


def render_snapshot_html(snap: dict, tenant: Tenant, version: int) -> str:
    """Una version enviada de la cotizacion, tal como salio (cliente, lineas y totales de ese momento)."""
    d = {**snap, "status": f"versión {version}", "consecutive": None, "clave": None}
    return TEMPLATE.render(
        band=None,
        clave_label="Clave",
        d=d,
        t=tenant,
        c=snap.get("customer"),
        kind=f"Cotización · v{version}",
        leyenda=LEYENDA,
        balance=None,
        footer=snap.get("footer"),
        **brand_ctx(tenant),
    )


def render_pdf(html: str) -> bytes | None:
    """Devuelve None si WeasyPrint no esta disponible (Windows sin GTK); la API entonces sirve HTML imprimible."""
    try:
        from weasyprint import HTML  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    return HTML(string=html).write_pdf()
