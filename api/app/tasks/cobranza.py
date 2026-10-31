"""Cobranza de facturas de punta a punta (plan 1.3): vencimiento, secuencia de recordatorios y resumen de cartera.

Tareas del worker (worker/celery_app.py solo las importa y las agenda):
- sales.mark_overdue (00:30 CR): factura COBRABLE con la fecha pasada -> "vencida". Nunca un borrador sin enviar.
- sales.reminders (08:00 CR): secuencia D-3, D0, D+3, D+10 (configurable) con el PDF, el enlace de pago y, si hay
  comprobante real, el XML.
- cobranza.resumen_semanal (lunes 07:00 CR): la cartera al admin, comparada con la del dia 0.

Reglas de la secuencia (Ajustes -> Facturación -> Cobranza):
- Sale solo con notify_due encendido, con el correo configurado y con la fecha de corte de cartera
  (collections_start_date). Sin fecha de corte no sale nada: la cartera vieja pudo pagarse por fuera. Antes de
  encenderla, Andres aprueba la corrida en seco (GET /collections/preview).
- Solo facturas COBRABLE ya enviadas (sent_at) con fecha de emision igual o posterior a la de corte.
- Una etapa por factura y por corrida: la mas reciente cuya fecha ya llego (si el worker estuvo caido no se mandan las
  atrasadas de golpe) y posterior al dia del envio (no se le recuerda lo que recibio ese mismo dia).
- Idempotente: una fila de collection_reminder por factura y etapa, escrita antes de mandar el correo
  (dedupe_key "rem:<factura>:<etapa>" cuando el outbox la soporte).
- Pausa por cliente (Customer.no_reminders) y por factura (Invoice.reminders_paused). Con saldo 0 se corta sola.
- Mensualidades de aliados: etapas propias (collections_ally_stages: el aviso de atraso a D+5 por defecto).
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from celery import shared_task
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.texto import forma, plural
from ..models import AllyCompany, CollectionReminder, Customer, Invoice, Payment, Quote, Tenant, TenantUser
from ..services.ally_access import PLAN_ORDER, SUSPEND_DAYS, mora_where
from ..services.invoice_states import cobrable_where, cr_today, vencible_where
from ..services.mail import mail_configured, member_inbox
from ..services.render import money

log = logging.getLogger("crimson.cobranza")

DEFAULT_STAGES = (-3, 0, 3, 10)
DEFAULT_ALLY_STAGES = (-3, 0, 5, 10)  # el aviso al aliado a D+5 (reunion 13/10; Andres lo confirma en 0.11)
STAGE_MIN, STAGE_MAX = -30, 120
MAX_STAGES = 8


def stage_label(k: int) -> str:
    return "D0" if k == 0 else f"D{k:+d}"


def parse_stages(raw, default: tuple[int, ...]) -> tuple[int, ...]:
    try:
        vals = sorted({int(x) for x in (raw or [])})
    except (TypeError, ValueError):
        vals = []
    vals = [v for v in vals if STAGE_MIN <= v <= STAGE_MAX][:MAX_STAGES]
    return tuple(vals) or default


def config(t: Tenant) -> dict:
    st = t.settings or {}
    start = st.get("collections_start_date")
    try:
        start = date.fromisoformat(str(start)) if start else None
    except ValueError:
        start = None
    return {
        "notify_due": bool(st.get("notify_due", True)),
        "start_date": start,
        "stages": parse_stages(st.get("collections_stages"), DEFAULT_STAGES),
        "ally_stages": parse_stages(st.get("collections_ally_stages"), DEFAULT_ALLY_STAGES),
    }


def is_plan(inv: Invoice) -> bool:
    return inv.external_order == PLAN_ORDER


def due_stage(inv: Invoice, stages: tuple[int, ...], today: date) -> int | None:
    """La etapa mas reciente cuya fecha ya llego, si es posterior al dia en que la factura le llego al cliente."""
    from ..services.documents import local_date

    if inv.due_date is None:
        return None
    llegadas = [k for k in stages if inv.due_date + timedelta(days=k) <= today]
    if not llegadas:
        return None
    k = max(llegadas)
    enviada = local_date(inv.sent_at)
    if enviada is not None and inv.due_date + timedelta(days=k) <= enviada:
        return None
    return k


# ---------- quien recibe que ----------
@dataclass
class Candidate:
    inv: Invoice
    customer: Customer | None
    stage: int
    skip: str | None = None  # motivo si no sale (pausa, sin correo)

    @property
    def stage_date(self) -> date:
        return self.inv.due_date + timedelta(days=self.stage)


def candidates(db: Session, t: Tenant, today: date, cfg: dict | None = None, start: date | None = None) -> list[Candidate]:
    """Facturas a las que hoy les toca una etapa que todavia no salio. start: fecha de corte (la de Ajustes si no)."""
    cfg = cfg or config(t)
    start = start or cfg["start_date"]
    if start is None:
        return []
    lo = min(min(cfg["stages"]), min(cfg["ally_stages"]))
    rows = db.scalars(
        select(Invoice)
        .where(
            Invoice.tenant_id == t.id,
            cobrable_where(),
            Invoice.sent_at.is_not(None),
            Invoice.due_date.is_not(None),
            Invoice.issue_date >= start,
            Invoice.due_date <= today - timedelta(days=lo),
        )
        .order_by(Invoice.due_date, Invoice.id)
    ).all()
    if not rows:
        return []
    hechas = set(db.execute(select(CollectionReminder.invoice_id, CollectionReminder.stage).where(CollectionReminder.invoice_id.in_([i.id for i in rows]))).all())
    out: list[Candidate] = []
    for inv in rows:
        k = due_stage(inv, cfg["ally_stages"] if is_plan(inv) else cfg["stages"], today)
        if k is None or (inv.id, k) in hechas:
            continue
        c = db.get(Customer, inv.customer_id) if inv.customer_id else None
        if c is not None and c.tenant_id != t.id:
            c = None  # nunca el correo de un cliente de otra empresa
        skip = None
        if inv.reminders_paused:
            skip = "Factura en pausa"
        elif c is not None and c.no_reminders:
            skip = "Cliente sin recordatorios"
        elif c is None or not (c.email or "").strip():
            skip = "El cliente no tiene correo"
        out.append(Candidate(inv, c, k, skip))
    return out


# ---------- el correo ----------
def _fmt(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def reminder_email(t: Tenant, inv: Invoice, stage: int, today: date, link: str | None, attached: bool) -> tuple[str, str]:
    from ..services.render import AVISO_DE_COBRO, doc_kind

    e = html.escape
    saldo = money(inv.balance, inv.currency)
    # el mismo nombre que el primer correo y el PDF (render.doc_kind): factura o aviso de cobro (recurrencia sin
    # comprobante fiscal); la mensualidad de aliados se nombra como tal
    kind = "mensualidad" if is_plan(inv) else doc_kind(inv)
    art = "el" if kind == AVISO_DE_COBRO else "la"
    que = f"la mensualidad de la plataforma <b>{e(inv.number)}</b>" if is_plan(inv) else f"{art} {kind} <b>{e(inv.number)}</b>"
    dias = (today - inv.due_date).days
    if dias < 0:
        subject = f"Recordatorio: {kind} {inv.number} vence el {_fmt(inv.due_date)}"
        intro = f"Le recordamos que {que} por <b>{saldo}</b> vence el <b>{_fmt(inv.due_date)}</b>."
    elif dias == 0:
        subject = f"Hoy vence {art} {kind} {inv.number}"
        intro = f"Hoy vence {que}. El saldo pendiente es de <b>{saldo}</b>."
    else:
        subject = f"{kind[:1].upper()}{kind[1:]} {inv.number} con {dias} día{'s' if dias != 1 else ''} de atraso"
        intro = f"{que[:1].upper()}{que[1:]} venció el <b>{_fmt(inv.due_date)}</b> y tiene un saldo pendiente de <b>{saldo}</b>."
    from ..services import email_layout as el

    if dias < 0:
        titulo = "Recordatorio de pago"
    elif dias == 0:
        titulo = "Hoy vence su pago"
    else:
        titulo = f"Pago con {dias} día{'s' if dias != 1 else ''} de atraso"
    boton = {"factura": "Pagar factura", "mensualidad": "Pagar mensualidad"}.get(kind, "Pagar en línea")
    body = (
        el.p_html(intro)
        + el.data_rows(
            [("Documento", f"{kind[:1].upper()}{kind[1:]} {inv.number}"), ("Venció el" if dias > 0 else "Vence el", _fmt(inv.due_date))],
            total=("Saldo pendiente", saldo),
        )
        + el.p("Si ya realizó el pago, le agradecemos ignorar este mensaje o responderlo con el comprobante para aplicarlo.", muted=True)
        + (el.p("El documento va adjunto en PDF.", small=True) if attached else "")
    )
    body = el.render(
        t,
        title=titulo,
        body=body,
        preheader=f"{subject}. Saldo pendiente: {saldo}.",
        action=(link, boton) if link else None,
        foot="Cualquier consulta, responda a este correo.",
    )
    return f"{subject} · {t.name}", body


def send_reminder(db: Session, t: Tenant, cand: Candidate, today: date) -> str | None:
    """Reclama la etapa (fila unica) y manda el aviso. None si otra corrida ya la tomo. No hace commit."""
    from ..services.documents import audit, get_or_create_payment_link
    from ..services.documents_send import fiscal_files, pdf_of, queue_once

    inv, k = cand.inv, cand.stage
    label = stage_label(k)
    sp = db.begin_nested()
    try:
        row = CollectionReminder(
            tenant_id=t.id, invoice_id=inv.id, stage=k, sent_on=today, due_date=inv.due_date, balance=inv.balance, currency=inv.currency, status="pendiente"
        )
        db.add(row)
        db.flush()
        sp.commit()
    except IntegrityError:
        sp.rollback()
        return None
    link = get_or_create_payment_link(db, t.id, inv).url
    pdf = pdf_of(db, t, inv)
    files = ([(f"{inv.number}.pdf", pdf)] if pdf else []) + fiscal_files(db, inv, real_only=True)
    subject, body = reminder_email(t, inv, k, today, link, attached=pdf is not None)
    m = queue_once(
        db,
        t,
        cand.customer.email.strip(),
        subject,
        body,
        "invoice",
        inv.id,
        [{"name": n, "kind": "invoices", "ref": inv.id, "stage": f"rem:{label}"} for n, _ in files],
        files=files or None,
        dedupe_key=f"rem:{inv.id}:{label}",
    )
    if m.status == "error" and files:
        m.status = "fallido"  # mail.retry_outbox lo mandaria sin el PDF: se avisa al admin y se reenvia desde la factura
    row.status = "enviado" if m.status == "enviado" else ("error" if m.status in ("error", "fallido") else m.status)
    row.outbox_id = m.id
    audit(db, t.id, None, "reminder", "invoice", inv.id, {"etapa": label, "correo": row.status})
    return row.status


def failed_reminders_html(t: Tenant, fallidas: list[str]) -> tuple[str, str]:
    """(asunto, cuerpo) del aviso al admin con los recordatorios de cobro que no salieron hoy."""
    from ..core.config import settings
    from ..services import email_layout as el

    asunto = f"Cobranza: {plural(len(fallidas), 'recordatorio no salió', 'recordatorios no salieron')}"
    cuerpo = el.render(
        t,
        title="Recordatorios de cobro sin salir",
        body=el.p(f"{forma(len(fallidas), 'Este recordatorio de cobro no salió', 'Estos recordatorios de cobro no salieron')} hoy:") + el.items(fallidas[:30]) + el.p("Revise el correo del cliente y reenvíe desde la factura."),
        preheader=asunto,
        action=(f"{settings.public_base_url.rstrip('/')}/facturas", "Abrir Facturas"),
        foot="Aviso interno de cobranza.",
    )
    return asunto, cuerpo


def run_reminders(db: Session, today: date | None = None) -> dict:
    """Corrida diaria de la secuencia. Cada factura en su propia transaccion: una que falla no frena a las demas."""
    from ..services.mail import notify_roles

    today = today or cr_today()
    out = {"enviados": 0, "fallidos": 0, "omitidos": 0}
    if not mail_configured():
        # sin correo todo quedaria "simulado" y la etapa se gastaria sin llegarle a nadie
        log.warning("cobranza: sin servicio de correo (RESEND_API_KEY): no se mandan recordatorios")
        return {**out, "sin_correo": True}
    for tid in db.scalars(select(Tenant.id).where(Tenant.active)).all():
        t = db.get(Tenant, tid)
        cfg = config(t)
        if not cfg["notify_due"] or cfg["start_date"] is None:
            continue
        fallidas: list[str] = []
        for cand in candidates(db, t, today, cfg):
            if cand.skip:
                out["omitidos"] += 1
                continue
            numero = cand.inv.number
            try:
                st = send_reminder(db, t, cand, today)
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
                log.exception("cobranza: recordatorio de %s", numero)
                st = "error"
            if st == "enviado":
                out["enviados"] += 1
            elif st is not None:
                out["fallidos"] += 1
                fallidas.append(numero)
        if fallidas:
            asunto, cuerpo = failed_reminders_html(t, fallidas)
            notify_roles(db, t, ("admin",), asunto, cuerpo, "invoice", 0)
            db.commit()
    return out


def preview(db: Session, t: Tenant, fecha: date) -> dict:
    """Corrida en seco: quien recibiria que en esa fecha. No escribe nada (ni enlaces de pago)."""
    cfg = config(t)
    warnings = []
    if cfg["start_date"] is None:
        warnings.append("Falta la fecha de corte de cartera: sin ella el worker no manda ningún recordatorio. Abajo se muestran todas las facturas enviadas.")
    if not cfg["notify_due"]:
        warnings.append("Los recordatorios al cliente están apagados: esto es solo la vista previa.")
    if not mail_configured():
        warnings.append("No hay servicio de correo configurado: el worker no manda recordatorios.")
    items, skipped, ejemplo = [], [], None
    for cand in candidates(db, t, fecha, cfg, start=cfg["start_date"] or date.min):
        inv, c = cand.inv, cand.customer
        subject, body = reminder_email(t, inv, cand.stage, fecha, "#", attached=True)
        row = {
            "invoice_id": inv.id,
            "number": inv.number,
            "customer": c.name if c else None,
            "email": (c.email if c else None) or None,
            "stage": stage_label(cand.stage),
            "stage_date": cand.stage_date,
            "due_date": inv.due_date,
            "days": (fecha - inv.due_date).days,
            "balance": inv.balance,
            "currency": inv.currency,
            "mensualidad": is_plan(inv),
            "subject": subject,
        }
        if cand.skip:
            skipped.append({**row, "reason": cand.skip})
        else:
            items.append(row)
            if ejemplo is None:
                ejemplo = {"subject": subject, "html": body}
    return {
        "fecha": fecha,
        "notify_due": cfg["notify_due"],
        "start_date": cfg["start_date"],
        "stages": [stage_label(k) for k in cfg["stages"]],
        "ally_stages": [stage_label(k) for k in cfg["ally_stages"]],
        "mail_configured": mail_configured(),
        "warnings": warnings,
        "items": items,
        "skipped": skipped,
        "ejemplo": ejemplo,
    }


# ---------- vencimiento ----------
def mark_overdue(db: Session, today: date | None = None) -> int:
    """Cotizaciones abiertas y facturas VENCIBLE con la fecha pasada -> "vencida" (un borrador nunca)."""
    today = today or cr_today()
    n = 0
    qids = db.scalars(select(Quote.id).where(Quote.status.in_(("creado", "enviada")), Quote.due_date < today)).all()
    if qids:
        db.execute(update(Quote).where(Quote.id.in_(qids)).values(status="vencida"))
        n += len(qids)
    iids = db.scalars(select(Invoice.id).where(vencible_where(today))).all()
    if iids:
        db.execute(update(Invoice).where(Invoice.id.in_(iids)).values(status="vencida"))
        n += len(iids)
    db.commit()
    return n


# ---------- cartera ----------
def _crc(t: Tenant, inv: Invoice, amount) -> Decimal:
    base = t.default_currency or "CRC"
    a = Decimal(str(amount or 0))
    return a if inv.currency == base else a * Decimal(str(inv.fx_sell or 1))


BUCKETS = (("al_dia", "Por vencer"), ("1_30", "1 a 30 días"), ("31_60", "31 a 60 días"), ("61_90", "61 a 90 días"), ("90_mas", "Más de 90 días"))


def _bucket(dias: int) -> str:
    if dias <= 0:
        return "al_dia"
    if dias <= 30:
        return "1_30"
    if dias <= 60:
        return "31_60"
    return "61_90" if dias <= 90 else "90_mas"


def portfolio(db: Session, t: Tenant, today: date) -> dict:
    """Cartera COBRABLE en la moneda de la empresa, con antiguedad. "vencida" = fecha de vencimiento pasada."""
    rows = db.scalars(select(Invoice).where(Invoice.tenant_id == t.id, cobrable_where()).order_by(Invoice.due_date, Invoice.id)).all()
    buckets = {k: Decimal(0) for k, _ in BUCKETS}
    total = vencida = Decimal(0)
    top = []
    for inv in rows:
        monto = _crc(t, inv, inv.balance)
        dias = (today - inv.due_date).days if inv.due_date else 0
        total += monto
        buckets[_bucket(dias)] += monto
        if dias > 0:
            vencida += monto
            top.append((dias, inv, monto))
    top.sort(key=lambda x: (-x[2], -x[0]))
    q2 = Decimal("0.01")
    return {
        "total": total.quantize(q2),
        "vencida": vencida.quantize(q2),
        "n": len(rows),
        "n_vencidas": len(top),
        "buckets": {k: v.quantize(q2) for k, v in buckets.items()},
        "top": [(dias, inv, monto.quantize(q2)) for dias, inv, monto in top[:10]],
    }


LINK_PROVIDERS = ("onvo",)  # cobros que entran por el enlace de pago (PayPal esta apagado en /public/pay)


def recovered_after_reminders(db: Session, t: Tenant, a: date, b: date) -> dict[str, Decimal]:
    """Cobrado entre a y b (inclusive) en facturas que ya habian recibido un recordatorio ese dia o antes (C.2).

    "enlace" es la cobranza recuperada: colones cobrados POR EL ENLACE de pago despues de un recordatorio (la cifra de
    valor con la que se cobra el servicio, nunca inflada). "otros" es lo que entro despues de un aviso por SINPE,
    transferencia o efectivo registrado a mano: se muestra aparte y no cuenta como recuperado."""
    out = {"enlace": Decimal(0), "otros": Decimal(0)}
    pagos = db.scalars(
        select(Payment).where(Payment.tenant_id == t.id, Payment.kind == "captura", Payment.status == "confirmado", Payment.paid_at >= a, Payment.paid_at <= b)
    ).all()
    for p in pagos:
        primero = db.scalar(
            select(func.min(CollectionReminder.sent_on)).where(CollectionReminder.invoice_id == p.invoice_id, CollectionReminder.status == "enviado")
        )
        if primero is not None and primero <= p.paid_at:
            inv = db.get(Invoice, p.invoice_id)
            out["enlace" if p.provider in LINK_PROVIDERS else "otros"] += _crc(t, inv, p.amount)
    return {k: v.quantize(Decimal("0.01")) for k, v in out.items()}


def _customer_name(db: Session, inv: Invoice) -> str:
    c = db.get(Customer, inv.customer_id) if inv.customer_id else None
    return c.name if c is not None and c.tenant_id == inv.tenant_id else ""


def _fecha_iso(v) -> str:
    """'2026-10-21' (como se guarda la línea base) -> '21/10/2026' para el correo."""
    try:
        return _fmt(date.fromisoformat(str(v)))
    except ValueError:
        return str(v or "")


def set_baseline(t: Tenant, today: date, vencida: Decimal) -> dict:
    """Cartera vencida del dia 0 (linea base del cobro de JC): con ella se compara cada resumen."""
    base = {"date": today.isoformat(), "overdue": str(vencida), "currency": t.default_currency or "CRC"}
    t.settings = {**(t.settings or {}), "collections_baseline": base}
    return base


def weekly_summary(db: Session, today: date | None = None) -> int:
    """Resumen semanal de cartera al admin (lunes 07:00 CR). La primera vez registra la linea base del dia 0."""
    from ..services.documents_send import queue_once, queued_before

    today = today or cr_today()
    e = html.escape
    n = 0
    semana = today.isocalendar()
    for tid in db.scalars(select(Tenant.id).where(Tenant.active)).all():
        t = db.get(Tenant, tid)
        p = portfolio(db, t, today)
        base = (t.settings or {}).get("collections_baseline")
        if not p["n"] and not base:
            continue
        nueva = base is None
        if nueva:
            base = set_baseline(t, today, p["vencida"])
        cur = t.default_currency or "CRC"
        desde = today - timedelta(days=7)
        avisos = db.execute(
            select(CollectionReminder.status, func.count()).where(CollectionReminder.tenant_id == t.id, CollectionReminder.sent_on >= desde, CollectionReminder.sent_on < today).group_by(CollectionReminder.status)
        ).all()
        enviados = sum(c for s, c in avisos if s == "enviado")
        fallidos = sum(c for s, c in avisos if s != "enviado")
        recuperado = recovered_after_reminders(db, t, desde, today - timedelta(days=1))
        dia0 = Decimal(str(base.get("overdue") or 0))
        dif = p["vencida"] - dia0
        comparacion = (
            "Línea base registrada hoy (día 0)."
            if nueva
            else f"Día 0 ({e(_fecha_iso(base.get('date')))}): {money(dia0, cur)} de cartera vencida · hoy {money(p['vencida'], cur)} "
            f"({'+' if dif > 0 else ''}{money(dif, cur)}{f', {dif / dia0 * 100:+.1f} %' if dia0 else ''})."
        )
        from ..core.config import settings
        from ..services import email_layout as el

        filas = [[inv.number, _customer_name(db, inv), str(dias), money(monto, cur)] for dias, inv, monto in p["top"]]
        aliados = db.scalars(select(AllyCompany).where(*mora_where(t.id, today, start=config(t)["start_date"])).order_by(AllyCompany.name)).all()
        mora = (
            el.p_html(
                f"<b>Aliados con {SUSPEND_DAYS} días o más de atraso</b> (propuesta de suspensión en Aprobaciones): "
                f"{e(', '.join(a.name for a in aliados))}."
            )
            if aliados
            else ""
        )
        otros = (
            f" Por otros medios después de un aviso (SINPE, transferencia o efectivo, no cuenta como recuperado): {money(recuperado['otros'], cur)}."
            if recuperado["otros"]
            else ""
        )
        body = (
            el.data_rows(
                [
                    ("Por cobrar", f"{money(p['total'], cur)} · {plural(p['n'], 'factura', 'facturas')}"),
                    ("Vencida", f"{money(p['vencida'], cur)} · {plural(p['n_vencidas'], 'factura', 'facturas')}"),
                ]
            )
            + el.p_html(comparacion)
            + el.p_html(
                f"Últimos 7 días: {plural(enviados, 'recordatorio enviado', 'recordatorios enviados')}{f', {fallidos} sin salir' if fallidos else ''}; "
                f"cobranza recuperada (cobrado por el enlace de pago después de un recordatorio): <b>{money(recuperado['enlace'], cur)}</b>.{otros}"
            )
            + el.heading("Antigüedad")
            + el.table(["Tramo", "Saldo"], [[lbl, money(p["buckets"][k], cur)] for k, lbl in BUCKETS], right=(1,))
            + (el.heading("Las 10 vencidas más grandes") + el.table(["Factura", "Cliente", "Días", "Saldo"], filas, right=(2, 3)) if filas else "")
            + mora
        )
        body = el.render(
            t,
            title=f"Cartera por cobrar · {today:%d/%m/%Y}",
            body=body,
            preheader=f"Por cobrar {money(p['total'], cur)}; vencida {money(p['vencida'], cur)}.",
            action=(f"{settings.public_base_url.rstrip('/')}/facturas", "Ver facturas vencidas"),
            foot="Resumen semanal de cartera para administración.",
        )
        asunto = f"Cartera de la semana · {today:%d/%m/%Y}"
        vistos: set[str] = set()
        for m in db.scalars(select(TenantUser).where(TenantUser.tenant_id == t.id, TenantUser.active, TenantUser.role_code == "admin")):
            correo = member_inbox(m)
            if not correo or correo in vistos or queued_before(db, t.id, correo, asunto):
                continue  # doble disparo del beat: el resumen de esta semana ya salio
            vistos.add(correo)
            queue_once(db, t, correo, f"{asunto} · {t.name}", body, "report", 0, dedupe_key=f"cartera:{t.id}:{semana.year}-{semana.week}:{correo}")
            n += 1
        db.commit()
    return n


# ---------- tareas del worker ----------
@shared_task(name="sales.mark_overdue")
def mark_overdue_task() -> int:
    from ..core.db import SessionLocal

    with SessionLocal() as db:
        return mark_overdue(db)


@shared_task(name="sales.reminders")
def reminders_task() -> dict:
    from ..core.db import SessionLocal

    with SessionLocal() as db:
        return run_reminders(db)


@shared_task(name="cobranza.resumen_semanal")
def weekly_summary_task() -> int:
    from ..core.db import SessionLocal

    with SessionLocal() as db:
        return weekly_summary(db)
