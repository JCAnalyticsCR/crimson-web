"""Tareas del guardian de margen (plan 1.12; la logica vive en services/margin_guard).

- margin.guard: revisa todas las empresas. Ya corre solo despues de cada refresco de Eurocomp y de cada tasa nueva;
  esta tarea queda como red (una vez al dia, antes del resumen) y para lanzarla a mano.
- margin.daily_summary: correo diario al admin con lo que esta bajo el minimo (fuera de venta, solo informe, sobre el
  tope, dudoso). Una vez por dia y por empresa aunque el beat dispare dos veces; nada que decir = no hay correo.

Las funciones reciben la sesion (se prueban sin Celery); las tareas solo abren la sesion y las llaman.
"""

from __future__ import annotations

import logging

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..models import Tenant
from ..services import margin_guard
from ..services.fx import cr_today

log = logging.getLogger("crimson.worker.margen")


def guard_all(db: Session) -> dict:
    return margin_guard.run_all(db, reason="revisión diaria")


def daily_summary(db: Session) -> int:
    """Un correo por empresa con productos bajo el minimo. Devuelve cuantas empresas recibieron el resumen."""
    from ..services.mail import notify_roles

    hoy = cr_today().isoformat()
    n = 0
    for t in db.scalars(select(Tenant).where(Tenant.active)).all():
        try:
            st = dict(t.settings or {})
            if st.get("margin_guard_summary_on") == hoy:
                continue  # ya salio hoy (beat duplicado o reintento)
            msg = margin_guard.summary_html(db, t, settings.public_base_url)
            if msg is None:
                continue
            subject, body = msg
            notify_roles(db, t, ("admin",), f"{subject} · {t.name}", body, "product", 0)
            st["margin_guard_summary_on"] = hoy
            t.settings = st
            db.commit()
            n += 1
        except Exception:  # noqa: BLE001  una empresa con datos raros no frena a las demas
            db.rollback()
            log.exception("resumen de margen (empresa %s)", t.id)
    return n


@shared_task(name="margin.guard")
def margin_guard_task() -> dict:
    from ..core.db import SessionLocal

    with SessionLocal() as db:
        return {str(k): v for k, v in guard_all(db).items()}


@shared_task(name="margin.daily_summary")
def margin_daily_summary_task() -> int:
    from ..core.db import SessionLocal

    with SessionLocal() as db:
        return daily_summary(db)
