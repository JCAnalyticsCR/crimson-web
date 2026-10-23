"""Agenda y tareas del vendedor (ventas etapa 3): llamadas, visitas y recordatorios ligados a un cliente, una
oportunidad o una cotizacion. La hora se guarda en UTC y se escribe/muestra en hora de Costa Rica.

Visibilidad (la misma de la etapa 2): quien no ve las ventas de todos (el vendedor) solo ve sus tareas y solo puede
ligarlas a un cliente que ve (cartera con dueno), a una oportunidad suya o a una cotizacion suya. Admin y jefe de
ventas ven las de todo el equipo y se las asignan a cualquier vendedor.

Resumen de la manana: 06:45 de lunes a sabado (beat "agenda-diaria", fuera de la franja 07:00-09:00 donde ya corren los
demas avisos), un correo por vendedor con lo de hoy y lo atrasado. Solo si tiene algo y si la empresa no lo apago.
"""

from __future__ import annotations

import html as htmllib
from datetime import UTC, date, datetime, time, timedelta

from fastapi import HTTPException
from sqlalchemy import select, true
from sqlalchemy.orm import Session

from ..models import Customer, Opportunity, Quote
from ..models.ventas3 import SalesTask
from .documents import CR, audit, local_date

KINDS = ("llamada", "visita", "recordatorio", "facturar")
KIND_LABEL = {"llamada": "Llamada", "visita": "Visita", "recordatorio": "Recordatorio", "facturar": "Facturar"}


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


def day_end_utc(day: date) -> datetime:
    return datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=CR).astimezone(UTC)


def day_start_utc(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=CR).astimezone(UTC)


def scope(p):
    return true() if p.sees_all_sales else SalesTask.assigned_to == p.user.id


def check_links(db: Session, p, customer_id: int | None, opportunity_id: int | None, quote_id: int | None) -> tuple[int | None, int | None, int | None]:
    """Valida lo ligado con el alcance de quien crea la tarea. Si viene la oportunidad o la cotizacion y no el
    cliente, se toma el de ellas."""
    from .cartera import can_see
    from .ownership import owns_doc

    tid = p.tenant.id
    if quote_id:
        q = db.get(Quote, quote_id)
        if not q or q.tenant_id != tid or not owns_doc(q, p):
            raise HTTPException(404, "Cotización no encontrada")
        customer_id = customer_id or q.customer_id
    if opportunity_id:
        o = db.get(Opportunity, opportunity_id)
        if not o or o.tenant_id != tid or (not p.can("crm_pipeline", "ver_todo") and o.owner_id != p.user.id):
            raise HTTPException(404, "Oportunidad no encontrada")
        customer_id = customer_id or o.customer_id
    if customer_id:
        c = db.get(Customer, customer_id)
        if not c or c.tenant_id != tid or not can_see(db, p, c):
            raise HTTPException(404, "Cliente no encontrado")
    return customer_id, opportunity_id, quote_id


def assignee(db: Session, p, uid: int | None) -> int:
    """El vendedor solo se asigna a si mismo; admin y jefe, a cualquiera que pueda vender."""
    from .ownership import eligible_seller

    if uid is None or uid == p.user.id:
        return p.user.id
    if not p.sees_all_sales:
        raise HTTPException(403, "Solo puede crear tareas para usted")
    if not eligible_seller(db, p.tenant.id, uid):
        raise HTTPException(422, "Ese usuario no puede recibir tareas de ventas")
    return uid


def get(db: Session, p, task_id: int) -> SalesTask:
    t = db.get(SalesTask, task_id)
    if not t or t.tenant_id != p.tenant.id or (not p.sees_all_sales and t.assigned_to != p.user.id):
        raise HTTPException(404, "Tarea no encontrada")
    return t


def out(db: Session, t: SalesTask) -> dict:
    from .cartera import owner_name

    now = datetime.now(UTC)
    due = _aware(t.due_at)
    c = db.get(Customer, t.customer_id) if t.customer_id else None
    o = db.get(Opportunity, t.opportunity_id) if t.opportunity_id else None
    q = db.get(Quote, t.quote_id) if t.quote_id else None
    return {
        "id": t.id,
        "kind": t.kind,
        "kind_label": KIND_LABEL.get(t.kind, t.kind),
        "title": t.title,
        "notes": t.notes,
        "due_at": due,
        "done_at": t.done_at,
        "overdue": t.done_at is None and due < now,
        "today": local_date(due) == local_date(now),
        "assigned_to": t.assigned_to,
        "assigned_name": owner_name(db, t.assigned_to),
        "customer": {"id": c.id, "name": c.name} if c else None,
        "opportunity": {"id": o.id, "number": o.number, "title": o.title} if o else None,
        "quote": {"id": q.id, "number": q.number} if q else None,
    }


def pending_today(db: Session, tenant_id: int, uid: int, day: date | None = None) -> list[SalesTask]:
    """Sin hacer y con hora antes de que termine el dia (lo de hoy y lo atrasado), lo mas viejo primero."""
    day = day or local_date(datetime.now(UTC))
    return list(
        db.scalars(
            select(SalesTask)
            .where(SalesTask.tenant_id == tenant_id, SalesTask.assigned_to == uid, SalesTask.done_at.is_(None), SalesTask.due_at < day_end_utc(day))
            .order_by(SalesTask.due_at, SalesTask.id)
        )
    )


def mark_done(db: Session, p, t: SalesTask, done: bool) -> None:
    t.done_at = datetime.now(UTC) if done else None
    t.done_by = p.user.id if done else None
    audit(db, p.tenant.id, p.user.id, "task_done" if done else "task_reopen", "sales_task", t.id, ip=p.ip)


def _hora(dt: datetime) -> str:
    return _aware(dt).astimezone(CR).strftime("%H:%M")


def digest(db: Session, tenant, now: datetime | None = None) -> int:
    """Un correo por vendedor con su agenda de hoy y lo atrasado. No hace commit."""
    from .avisos_venta import miembros
    from .email_layout import cta
    from .mail import queue_email
    from .sale_assignment import ventas_settings

    if not ventas_settings(tenant).get("resumen_diario_vendedores", True):
        return 0
    now = now or datetime.now(UTC)
    hoy = local_date(now)
    uids = set(
        db.scalars(
            select(SalesTask.assigned_to).where(SalesTask.tenant_id == tenant.id, SalesTask.done_at.is_(None), SalesTask.due_at < day_end_utc(hoy)).distinct()
        )
    )
    n = 0
    e = htmllib.escape
    for m in miembros(db, tenant, user_ids=list(uids)):
        tareas = pending_today(db, tenant.id, m.user.id, hoy)
        if not tareas or not m.inbox:
            continue
        atrasadas = [t for t in tareas if _aware(t.due_at) < day_start_utc(hoy)]
        de_hoy = [t for t in tareas if t not in atrasadas]

        def fila(t: SalesTask, atrasada: bool) -> str:
            c = db.get(Customer, t.customer_id) if t.customer_id else None
            cuando = local_date(_aware(t.due_at)).strftime("%d/%m") if atrasada else _hora(t.due_at)
            return f"<li><b>{e(cuando)}</b> · {e(KIND_LABEL.get(t.kind, t.kind))}: {e(t.title)}{f' · {e(c.name)}' if c else ''}</li>"

        partes = [f"<p>Buenos días, {e((m.user.full_name or '').split(' ')[0] or 'equipo')}. Esto es lo que tiene para hoy:</p>"]
        if de_hoy:
            partes.append("<ul>" + "".join(fila(t, False) for t in de_hoy[:25]) + "</ul>")
        if atrasadas:
            partes.append(f"<p><b>Atrasadas ({len(atrasadas)}):</b></p><ul>" + "".join(fila(t, True) for t in atrasadas[:25]) + "</ul>")
        partes.append(cta("/", "Abrir mi inicio"))
        queue_email(
            db, tenant, m.inbox, f"Su agenda de hoy · {len(tareas)} pendiente{'s' if len(tareas) != 1 else ''} · {tenant.name}", "".join(partes),
            "user", m.user.id, dedupe_key=f"agenda:{tenant.id}:{m.user.id}:{hoy.isoformat()}", company_bcc=False,
        )
        n += 1
    return n
