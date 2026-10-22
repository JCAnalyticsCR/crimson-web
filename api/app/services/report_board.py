"""Tablero de reportes (reunion 2, pedido 3: "reportes mas visuales, tipo dashboard"). Indicadores del periodo en
colones con el tipo de cambio de cada documento (services/profit_loss.Converter): ventas y su variacion contra el
periodo anterior de la misma duracion, ventas por vendedor y por tipo, ventas de los ultimos 6 meses, lo cobrado, los
productos mas vendidos y las cuentas por cobrar por antiguedad. El margen (utilidad bruta y neta del estado de
resultados) solo va a quien ve el estado de resultados (routers/report_board.py: la misma regla en todas las pantallas).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Customer, Invoice, Payment
from . import profit_loss as pl
from .invoice_states import cobrable_where, cr_today

AGING = (
    ("al_dia", "Al día", None, 0),
    ("d30", "1 a 30 días", 1, 30),
    ("d60", "31 a 60 días", 31, 60),
    ("d90", "61 a 90 días", 61, 90),
    ("mas90", "Más de 90 días", 91, None),
)
MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "set", "oct", "nov", "dic")


def _month_start(d: date, back: int = 0) -> date:
    y, m = d.year, d.month - back
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def _month_end(d: date) -> date:
    return _month_start(d, -1) - timedelta(days=1)


def _var(now: Decimal, before: Decimal) -> float | None:
    return round(float((now - before) / before * 100), 1) if before else None


def board(db: Session, tid: int, a: date, b: date, sees_pnl: bool) -> dict:
    conv = pl.Converter(db, "CRC")
    invs = pl.invoices_of(db, tid, a, b)
    inc = pl.income(db, tid, a, b, conv, invs)
    # periodo anterior de la misma duracion (oct 1-22 contra sep 9-30): la flecha de "como vamos"
    dias = (b - a).days + 1
    pa, pb = a - timedelta(days=dias), a - timedelta(days=1)
    antes = sum((x.sign * conv(pl.net_of(x.inv), x.inv.currency, x.inv.fx_sell, x.inv.issue_date) for x in pl.invoices_of(db, tid, pa, pb)), Decimal(0))

    # ultimos 6 meses hasta el mes de "to"
    desde = _month_start(b, 5)
    serie = {(_month_start(b, k).year, _month_start(b, k).month): Decimal(0) for k in range(5, -1, -1)}
    for x in pl.invoices_of(db, tid, desde, _month_end(b)):
        k = (x.on.year, x.on.month)  # la NC resta en su mes
        if k in serie:
            serie[k] += x.sign * conv(pl.net_of(x.inv), x.inv.currency, x.inv.fx_sell, x.inv.issue_date)

    # productos mas vendidos (venta sin IVA en colones)
    top: dict[tuple, list] = {}
    for x in invs:
        i = x.inv
        for ln in i.lines:
            k = (ln.code or "", ln.name)
            r = top.setdefault(k, [ln.code or "", ln.name, Decimal(0), Decimal(0)])
            r[2] += x.sign * pl.d(ln.quantity)
            r[3] += x.sign * conv(ln.subtotal, i.currency, i.fx_sell, i.issue_date)

    # cobrado en el periodo (capturas confirmadas menos devoluciones)
    cobrado = Decimal(0)
    for p in db.scalars(select(Payment).where(Payment.tenant_id == tid, Payment.paid_at >= a, Payment.paid_at <= b, Payment.status == "confirmado")):
        sign = 1 if p.kind == "captura" else (-1 if p.kind in ("devolucion", "reembolso") else 0)
        if sign:
            cobrado += sign * conv(p.amount, p.currency, None, p.paid_at)

    # cuentas por cobrar: la misma definicion que el inicio y la lista (services/invoice_states)
    hoy = cr_today()
    buckets = {k: {"key": k, "label": label, "amount": Decimal(0), "count": 0} for k, label, _, _ in AGING}
    deudores: dict[int | None, Decimal] = {}
    cxc_total, cxc_n = Decimal(0), 0
    for i in db.scalars(select(Invoice).where(Invoice.tenant_id == tid, cobrable_where())):
        saldo = conv(i.balance, i.currency, i.fx_sell, i.issue_date)
        cxc_total += saldo
        cxc_n += 1
        atraso = (hoy - i.due_date).days if i.due_date else 0
        for k, _, lo, hi in AGING:
            if (lo is None and atraso <= 0) or (lo is not None and atraso >= lo and (hi is None or atraso <= hi)):
                buckets[k]["amount"] += saldo
                buckets[k]["count"] += 1
                break
        deudores[i.customer_id] = deudores.get(i.customer_id, Decimal(0)) + saldo
    top_deudores = sorted(deudores.items(), key=lambda x: -x[1])[:5]
    cust = {c.id: c.name for c in db.scalars(select(Customer).where(Customer.id.in_([k for k, _ in top_deudores if k])))} if top_deudores else {}

    out = {
        "from": a,
        "to": b,
        "currency": "CRC",
        "ventas": {
            "total": inc["total"],
            "facturas": inc["facturas"],
            "anterior": pl.q2(antes),
            "variacion_pct": _var(inc["total"], antes),
            "iva_cobrado": inc["iva_cobrado"],
        },
        "ticket_promedio": pl.q2(inc["total"] / inc["facturas"]) if inc["facturas"] else Decimal(0),
        "por_vendedor": inc["por_vendedor"],
        "por_tipo": inc["por_tipo"],
        "serie": [{"label": f"{MESES[m - 1]} {str(y)[2:]}", "year": y, "month": m, "amount": pl.q2(v)} for (y, m), v in serie.items()],
        "top_productos": [{"code": r[0], "name": r[1], "quantity": r[2], "amount": pl.q2(r[3])} for r in sorted(top.values(), key=lambda r: -r[3])[:5]],
        "cobrado": pl.q2(cobrado),
        "cxc": {
            "total": pl.q2(cxc_total),
            "facturas": cxc_n,
            "vencido": pl.q2(sum((x["amount"] for k, x in buckets.items() if k != "al_dia"), Decimal(0))),
            "antiguedad": [{**x, "amount": pl.q2(x["amount"])} for x in buckets.values()],
            "principales": [{"customer_id": k, "name": cust.get(k, "Sin cliente"), "amount": pl.q2(v)} for k, v in top_deudores],
        },
        "margen": None,
    }
    if sees_pnl:
        res = pl.build(db, tid, a, b, "CRC")
        out["margen"] = {
            "utilidad_bruta": res["utilidad_bruta"],
            "margen_bruto_pct": res["margen_bruto_pct"],
            "utilidad_neta": res["utilidad_neta"],
            "margen_neto_pct": res["margen_neto_pct"],
            "comisiones": res["comisiones"],
            "gastos": res["gastos_total"],
        }
    return out
