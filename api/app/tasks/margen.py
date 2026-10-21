"""Tareas del guardian de margen (plan 1.12; la logica vive en services/margin_guard).

- margin.guard: revisa todas las empresas. Ya corre solo despues de cada refresco de Eurocomp, de cada lista de
  proveedor y de cada tasa nueva (desde el portal, solo la empresa que la cargo); esta tarea queda para lanzarla a mano.
- margin.daily_summary (la unica entrada del beat, regla 4 del plan): primero corre el guardian como red (con
  BCCR_TOKEN vacio y sin credenciales de Eurocomp no hay refresco ni tasa nueva que lo dispare, y revisa las empresas
  que no cargaron la tasa) y despues manda el correo diario al admin con lo que esta bajo el minimo (fuera de venta,
  solo informe, frenado, dudoso), los saltos dudosos que siguen sobre el minimo y la alerta de tipo de cambio viejo o
  faltante. Una vez por dia y por empresa aunque el beat dispare dos veces; nada que decir = no hay correo. Sin BCC
  (mail.NO_BCC_ENTITIES): trae margenes y precios, de los que sale el costo.

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
            # entidad propia: el resumen trae margenes y precios (el costo se deduce) y no va al BCC de las facturas
            notify_roles(db, t, ("admin",), f"{subject} · {t.name}", body, "margin_summary", 0)
            # releer con bloqueo despues de enviar (Resend puede tardar): un PUT /settings de ese rato no se revierte
            margin_guard.save_settings(db, t.id, {"margin_guard_summary_on": hoy})
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
        guard_all(db)  # red diaria: run_all hace su propio commit por empresa y nunca lanza
        return daily_summary(db)
