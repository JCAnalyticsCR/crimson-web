"""Version impresa de un pedido de la tienda (reunion 21/10, item 38: "hacerla mas presentable").

Hoja carta con el mismo lenguaje que la factura en PDF (services/render.py): logo y datos de la empresa, numero y
estado del pedido, cliente, entrega y pago, lineas con precio sin IVA, IVA y total, totales y un pie para firmar el
retiro o la entrega. Se abre desde el panel como blob: el logo va incrustado (data URI) porque una ruta relativa no
resuelve dentro de un blob y la CSP de la hoja no deja pedir nada afuera salvo imagenes https.
"""

from __future__ import annotations

import base64
import html
import re
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Media, Order, Tenant
from . import store_orders as so

_KEY = re.compile(r"/media/f/([A-Za-z0-9_\-]+)")
MAX_LOGO = 1_500_000  # bytes: un logo mas pesado no se incrusta (la hoja sale con el nombre)


def logo_src(db: Session, t: Tenant) -> str | None:
    """Logo de la tienda (Mi Tienda) o de la empresa como data URI; una URL https externa se deja tal cual."""
    from ..routers.store import store_cfg

    url = (store_cfg(t).get("logo_url") or t.logo_url or "").strip()
    if not url:
        return None
    m = _KEY.search(url)
    if m:
        media = db.scalar(select(Media).where(Media.key == m.group(1), Media.tenant_id == t.id))
        if not media or not (media.content_type or "").startswith("image/") or (media.size or 0) > MAX_LOGO:
            return None
        return f"data:{media.content_type};base64,{base64.b64encode(media.data).decode()}"
    return url if url.startswith("https://") else None


def _q(v) -> str:
    return f"{Decimal(str(v or 0)).normalize():f}"


def render(db: Session, t: Tenant, o: Order) -> str:
    from ..routers.store import store_cfg

    e = html.escape
    cfg = store_cfg(t)
    primary = cfg.get("primary") or "#e2233a"
    if not re.fullmatch(r"#[0-9a-fA-F]{3,8}", str(primary)):
        primary = "#e2233a"
    c, dv = o.contact or {}, o.delivery or {}
    cur = o.currency
    money = lambda v: e(so.fmt_money(v, cur))  # noqa: E731
    logo = logo_src(db, t)
    name = cfg.get("name") or t.name
    retiro = dv.get("type") == "retiro"
    if retiro:
        pk = so.checkout_cfg(t).get("pickup") or {}
        # el titulo de la caja ya dice "Retiro en sitio" / el metodo de envio: aqui solo el donde y el cuando
        entrega = " · ".join(x for x in (pk.get("address"), pk.get("hours")) if x)
    else:
        entrega = ", ".join(x for x in (dv.get("address"), dv.get("district"), dv.get("canton"), dv.get("province")) if x) or (c.get("address") or "")
    estado = so.STATUS_LABEL.get(o.status, o.status)
    tono = "ok" if o.status in so.PAID_STATES else "bad" if o.status in ("cancelado", "reembolsado") else ""
    fecha = o.created_at.strftime("%d/%m/%Y %H:%M") if o.created_at else ""
    pagado = f"Pagado el {o.paid_at.strftime('%d/%m/%Y')}" if o.paid_at else ("Comprobante en revisión" if o.status == "en_revision" else "Pendiente de pago")

    rows = "".join(
        f"<tr><td><b>{e(ln.name)}</b></td><td class=num>{_q(ln.quantity)}</td><td class=num>{money(ln.unit_price)}</td>"
        f"<td class=num>{_q(ln.tax_rate)}%</td><td class=num>{money(ln.total)}</td></tr>"
        for ln in o.lines
        if not so._is_ship(ln)
    )
    desc = (
        f"<tr><td>Descuento{' (' + e(o.coupon_code) + ')' if o.coupon_code else ''}</td><td class=num>−{money(o.discount_total)}</td></tr>"
        if Decimal(str(o.discount_total or 0)) > 0
        else ""
    )
    empresa = " · ".join(x for x in (e(t.legal_name or ""), f"Cédula {e(t.tax_id)}" if t.tax_id else "", e(cfg.get("whatsapp") or "")) if x)
    marca = f'<img src="{e(logo)}" alt="{e(name)}">' if logo else ""
    cliente = "<br>".join(
        x
        for x in (
            f"<b>{e(c.get('name') or '—')}</b>",
            e(c.get("company") or ""),
            f"Céd. {e(c.get('id_number'))}" if c.get("id_number") else "",
            e(c.get("email") or ""),
            e(c.get("phone") or ""),
        )
        if x
    )
    pago = "<br>".join(
        x
        for x in (
            f"<b>{e(o.payment_method or 'A coordinar')}</b>",
            e(pagado),
            f"Ref. {e(o.payment_ref)}" if o.payment_ref else "",
        )
        if x
    )
    guia = f"<br>Guía <b class=mono>{e(o.tracking_number)}</b>{' · ' + e(o.carrier) if o.carrier else ''}" if o.tracking_number else ""
    nota = f"<div class=notes><div class=k>Nota del cliente</div>{e(o.notes)}</div>" if o.notes else ""
    motivo = f"<div class=notes><div class=k>Motivo de cancelación</div>{e(o.cancel_reason)}</div>" if o.cancel_reason and o.status == "cancelado" else ""
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pedido {e(o.number)} · {e(name)}</title>
<style>
@page {{ size: Letter; margin: 16mm 14mm; }}
*{{box-sizing:border-box}}
body{{font-family:Arial,Helvetica,sans-serif;color:#15131a;font-size:12.5px;line-height:1.45;margin:0;background:#f6f2ee}}
.sheet{{max-width:780px;margin:20px auto;background:#fff;padding:28px 32px;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.bar{{max-width:780px;margin:16px auto 0;display:flex;justify-content:flex-end}}
.bar button{{font:600 13px Arial,sans-serif;padding:9px 16px;border-radius:999px;border:0;background:{primary};color:#fff;cursor:pointer}}
.top{{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;border-bottom:3px solid {primary};padding-bottom:14px;margin-bottom:18px}}
.brand img{{max-height:46px;max-width:220px;display:block;margin-bottom:6px}}.brand b{{font-size:19px;letter-spacing:-.02em;display:block}}.brand span{{color:#5a5560;font-size:11px}}
.docid{{text-align:right}}.k{{font-family:Consolas,monospace;font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;color:#8a858f;margin-bottom:4px}}
.docid .n{{font-size:24px;font-weight:700;letter-spacing:-.02em}}.docid .d{{color:#5a5560;font-size:11.5px;margin:2px 0 6px}}
.stamp{{display:inline-block;padding:3px 10px;border-radius:999px;font-size:10.5px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;background:#f6f2ee;color:#5a5560}}
.stamp.ok{{background:#e4f6ea;color:#1a8a4b}}.stamp.bad{{background:#fde3e6;color:#c81e33}}
.meta{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:18px}}
.box{{border:1px solid #e6e1db;border-radius:8px;padding:10px 12px;overflow-wrap:anywhere}}
table{{width:100%;border-collapse:collapse}}th{{text-align:left;font-family:Consolas,monospace;font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;color:#8a858f;border-bottom:1px solid #e6e1db;padding:6px 4px}}
td{{padding:8px 4px;border-bottom:1px solid #f0ece7;vertical-align:top}}.num{{text-align:right;font-family:Consolas,monospace;white-space:nowrap}}.mono{{font-family:Consolas,monospace}}
.tot{{width:48%;margin-left:auto;margin-top:12px}}.tot td{{padding:5px 4px;border:0}}.tot .grand td{{border-top:2px solid #15131a;font-size:16px;font-weight:700;padding-top:8px}}
.notes{{margin-top:14px;padding:10px 12px;background:#f6f2ee;border-radius:8px;color:#5a5560;white-space:pre-wrap}}
.sign{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;margin-top:34px}}.sign div{{border-top:1px solid #15131a;padding-top:4px;font-size:10.5px;color:#5a5560}}
.foot{{margin-top:22px;padding-top:10px;border-top:1px solid #e6e1db;font-size:10px;color:#8a858f;display:flex;justify-content:space-between;gap:12px}}
@media (max-width:640px){{.sheet{{margin:0;border-radius:0;padding:18px 16px}}.meta,.sign{{grid-template-columns:1fr}}.top{{flex-direction:column}}.docid{{text-align:left}}.tot{{width:100%}}}}
@media print{{body{{background:#fff}}.bar{{display:none}}.sheet{{margin:0;max-width:none;box-shadow:none;padding:0;border-radius:0}}}}
</style></head><body>
<div class=bar><button onclick="print()">Imprimir</button></div>
<main class=sheet>
<div class=top>
  <div class=brand>{marca}<b>{e(name)}</b><span>{empresa}</span></div>
  <div class=docid><div class=k>Pedido de la tienda</div><div class=n>{e(o.number)}</div><div class=d>{e(fecha)}</div><span class="stamp {tono}">{e(estado)}</span></div>
</div>
<div class=meta>
  <div class=box><div class=k>Cliente</div>{cliente}</div>
  <div class=box><div class=k>Entrega</div><b>{"Retiro en sitio" if retiro else e(o.shipping_method or "Envío")}</b>{"<br>" + e(entrega) if entrega else ""}{guia}</div>
  <div class=box><div class=k>Pago</div>{pago}</div>
</div>
<table><thead><tr><th>Producto</th><th class=num>Cant.</th><th class=num>Precio s/IVA</th><th class=num>IVA</th><th class=num>Total</th></tr></thead><tbody>{rows}</tbody></table>
<table class=tot><tr><td>Subtotal (sin IVA)</td><td class=num>{money(o.subtotal)}</td></tr>{desc}
<tr><td>Envío (sin IVA)</td><td class=num>{money(o.shipping)}</td></tr>
<tr><td>IVA</td><td class=num>{money(o.tax_total)}</td></tr>
<tr class=grand><td>Total</td><td class=num>{money(o.total)}</td></tr></table>
{nota}{motivo}
<div class=sign><div>{"Retirado por" if retiro else "Recibido por"} (nombre)</div><div>Cédula</div><div>Firma y fecha</div></div>
<div class=foot><span>{e(name)}{" · " + e(t.tax_id) if t.tax_id else ""}</span><span>Pedido {e(o.number)} · {e(estado)}</span></div>
</main></body></html>"""
