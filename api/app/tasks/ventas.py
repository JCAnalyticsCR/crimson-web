"""Tareas del worker de ventas (plan maestro 1.6). worker/celery_app.py solo las importa y las programa en el beat.

Cotizaciones frias: Andres, "cotizacion lleva cinco dias sin seguimiento, que se le avise a la vendedora". Antes el
correo le llegaba a quien la tecleo (no a la vendedora), contaba desde la fecha de emision (no desde el ultimo envio),
incluia las que el cliente ya habia aceptado o rechazado y se repetia todos los dias.

PARA EL INTEGRADOR (al fusionar el 1.2): el "no repetir antes de 5 dias" vive hoy en la bitacora (ACCION_FRIA) y la
corrida se serializa por empresa (_un_solo_aviso). Con el 1.2 adentro, cada correo pasa ademas
dedupe_key=f"fria:{tenant.id}:{uid}:{hoy.isoformat()}" (uid 0 = administracion; hoy = fecha de Costa Rica): un doble
disparo del beat queda en un solo correo aunque falle el candado."""

from __future__ import annotations

import html
import logging
from datetime import UTC, datetime, time, timedelta

from celery import shared_task
from sqlalchemy import exists, func, select, text
from sqlalchemy.orm import Session

from ..models import AuditLog, Customer, Opportunity, Project, Quote, QuoteVersion
from ..services import avisos_venta as avisos
from ..services.documents import CR, audit, local_date
from ..services.email_layout import cta
from ..services.sale_assignment import WEB_SOURCES

log = logging.getLogger(__name__)

FRIA_DIAS = 5  # dias desde el ultimo envio sin respuesta del cliente
REPETIR_DIAS = 5  # una cotizacion fria no se vuelve a avisar antes de esto
ACCION_FRIA = "aviso_cotizacion_fria"  # bitacora de la cotizacion: hace de llave de deduplicacion (no se repite)
MAX_FILAS = 20
CANDADO_FRIAS = 1606  # pg_advisory_xact_lock(CANDADO_FRIAS, empresa): una corrida a la vez por empresa


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)  # SQLite devuelve la hora sin zona (es UTC)


def cotizaciones_frias(db: Session, tenant_id: int, ahora: datetime | None = None, dias: int = FRIA_DIAS) -> list[tuple[Quote, datetime]]:
    """Enviadas al cliente y sin su respuesta (ni aceptadas ni rechazadas), sin convertir, sin proyecto y sin una
    oportunidad ya cerrada (ganada o perdida: cerrarla en el embudo no toca la cotizacion, que seguia "enviada" y
    "pendiente"), cuyo ULTIMO envio fue hace `dias` o mas, en dias de calendario de Costa Rica (un envio de las 8 p. m.
    CR ya es del dia siguiente en UTC: contado en UTC saldria un dia tarde). Ultimo envio = el mas reciente entre una
    version enviada (QuoteVersion.sent_at) y un reenvio sin cambios (no crea version: queda en la bitacora como
    "resend_version"); las de antes de las versiones, su fecha de emision. Devuelve (cotizacion, ultimo envio), lo mas
    viejo primero."""
    ahora = ahora or datetime.now(UTC)
    corte = local_date(ahora) - timedelta(days=dias)
    ultimo = (
        select(QuoteVersion.quote_id, func.max(QuoteVersion.sent_at).label("enviada"))
        .where(QuoteVersion.tenant_id == tenant_id)
        .group_by(QuoteVersion.quote_id)
        .subquery()
    )
    con_proyecto = select(Project.quote_id).where(Project.tenant_id == tenant_id, Project.quote_id.is_not(None))
    cerrada = exists(
        select(Opportunity.id).where(Opportunity.tenant_id == tenant_id, Opportunity.quote_id == Quote.id, Opportunity.status.in_(("ganada", "perdida")))
    )
    filas = db.execute(
        select(Quote, ultimo.c.enviada)
        .outerjoin(ultimo, ultimo.c.quote_id == Quote.id)
        .where(
            Quote.tenant_id == tenant_id,
            Quote.status == "enviada",
            Quote.acceptance_status == "pendiente",
            Quote.id.not_in(con_proyecto),
            ~cerrada,
        )
    ).all()
    if not filas:
        return []
    # reenvios sin cambios, solo de las candidatas (la bitacora es grande)
    reenvios = dict(
        db.execute(
            select(AuditLog.entity_id, func.max(AuditLog.at))
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.entity == "quote",
                AuditLog.action == "resend_version",
                AuditLog.entity_id.in_([q.id for q, _ in filas]),
            )
            .group_by(AuditLog.entity_id)
        ).all()
    )
    out = []
    for q, enviada in filas:
        envios = [_aware(x) for x in (enviada, reenvios.get(q.id)) if x is not None]
        desde = max(envios) if envios else datetime.combine(q.issue_date, time(0), tzinfo=CR)
        if local_date(desde) <= corte:
            out.append((q, desde))
    return sorted(out, key=lambda x: (x[1], x[0].id))


def _un_solo_aviso(db: Session, tenant_id: int) -> None:
    """Dos disparos del beat a la vez (dos workers antes del candado de redbeat del 1.2) leerian la bitacora vacia y la
    vendedora recibiria dos correos. En Postgres la corrida de cada empresa se serializa con un candado de la
    transaccion (se suelta con el commit o el rollback de esa empresa): la segunda, al entrar, ya ve en la bitacora lo
    que aviso la primera. SQLite (pruebas) no lo tiene ni lo necesita."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:k, :t)"), {"k": CANDADO_FRIAS, "t": tenant_id})


def avisar_cotizaciones_frias(db: Session, tenant, ahora: datetime | None = None) -> int:
    """Un correo por vendedora con SUS cotizaciones frias (vendedor responsable; sin vendedor, quien la tecleo). Una
    cotizacion ya avisada no se repite antes de REPETIR_DIAS (queda en su bitacora). Si la vendedora ya no esta activa,
    la lista va a administracion para reasignarla; lo mismo si ya no puede ver esa cotizacion o sus precios (le
    cambiaron el rol: el correo lleva el total y enlaces que no podria abrir). Devuelve cuantas cotizaciones se
    avisaron. No hace commit (el commit de quien llama suelta el candado de _un_solo_aviso)."""
    ahora = ahora or datetime.now(UTC)
    _un_solo_aviso(db, tenant.id)
    frias = cotizaciones_frias(db, tenant.id, ahora)
    if not frias:
        return 0
    ids = [q.id for q, _ in frias]
    hoy = local_date(ahora)
    # avisada el dia D (calendario CR) no se repite hasta el dia D + REPETIR_DIAS, corra el beat un segundo antes o
    # despues (contado en horas, un aviso de las 07:45:10 se saltaba la corrida de las 07:45:05 cinco dias despues)
    desde_aviso = datetime.combine(hoy - timedelta(days=REPETIR_DIAS - 1), time(0), tzinfo=CR).astimezone(UTC)
    ya = set(
        db.scalars(
            select(AuditLog.entity_id).where(
                AuditLog.tenant_id == tenant.id,
                AuditLog.action == ACCION_FRIA,
                AuditLog.entity == "quote",
                AuditLog.entity_id.in_(ids),
                AuditLog.at >= desde_aviso,
            )
        )
    )
    pendientes = [(q, desde) for q, desde in frias if q.id not in ya]
    if not pendientes:
        return 0
    activos = {m.user.id: m for m in avisos.miembros(db, tenant, user_ids=[avisos.quote_owner(q) for q, _ in pendientes])}
    por_persona: dict[int, list[tuple[Quote, datetime]]] = {}
    for q, desde in pendientes:
        dueno = avisos.quote_owner(q)
        m = activos.get(dueno)
        puede = m is not None and avisos.ve_cotizacion(m, q) and m.can("catalog", "precios")
        por_persona.setdefault(dueno if puede else 0, []).append((q, desde))
    n = 0
    for uid, items in por_persona.items():
        destinos = [activos[uid]] if uid else avisos.miembros(db, tenant, roles=("admin",))

        def cuerpo(_m, items=items, uid=uid) -> str:
            filas = "".join(
                f'<li><a href="{html.escape(avisos.enlace(f"/cotizaciones/{q.id}"))}"><b>{html.escape(q.number)}</b></a> · '
                f"{html.escape(_cliente(db, q))} · {html.escape(q.currency)} {q.total:,.0f} · "
                f"enviada hace {(hoy - local_date(desde)).days} días</li>"
                for q, desde in items[:MAX_FILAS]
            )
            mas = f"<p>… y {len(items) - MAX_FILAS} más.</p>" if len(items) > MAX_FILAS else ""
            intro = (
                f"Llevan {FRIA_DIAS} días o más desde el último envío y el cliente no ha respondido:"
                if uid
                else "Estas cotizaciones están frías y su vendedora ya no está activa (o ya no las puede ver): reasígnelas o deles seguimiento:"
            )
            return (
                f"<p>{intro}</p><ul>{filas}</ul>{mas}"
                "<p>Una llamada a tiempo es la diferencia. Si el cliente ya respondió, regístrelo en Versiones de la cotización: "
                "así no vuelve a aparecer aquí.</p>"
                + (  # el boton principal del correo (email_layout)
                    cta(f"/cotizaciones/{items[0][0].id}", f"Abrir la cotización {items[0][0].number}")
                    if len(items) == 1
                    else cta("/cotizaciones", "Abrir Cotizaciones")
                )
            )

        asunto = f"{len(items)} cotización{'es' if len(items) != 1 else ''} sin respuesta del cliente"
        enviados = avisos.avisar(db, tenant, destinos, asunto, cuerpo, "quote", items[0][0].id)
        if not enviados:
            continue  # sin correo: se vuelve a intentar en la proxima corrida
        for q, _ in items:
            audit(db, tenant.id, None, ACCION_FRIA, "quote", q.id, {"para": uid or "admin", "dias": FRIA_DIAS})
        n += len(items)
    return n


def _cliente(db: Session, q: Quote) -> str:
    c = db.get(Customer, q.customer_id) if q.customer_id else None
    return c.name if c is not None and c.tenant_id == q.tenant_id else "sin cliente"


def avisar_consultas_sin_responsable(db: Session, tenant) -> int:
    """Correo diario al admin con las oportunidades de tienda web sin responsable y su antigüedad.
    Solo se envía si hay al menos una. Incluye las de owner_id NULL (el hito exacto que el coordinator
    menciona: 'hoy salta owner_id 0'). No hace commit."""
    from ..models import Opportunity
    from ..services.avisos_venta import miembros
    from ..services.documents import local_date
    from ..services.email_layout import cta

    filas = db.scalars(
        select(Opportunity).where(
            Opportunity.tenant_id == tenant.id,
            Opportunity.owner_id.is_(None),
            Opportunity.source.in_(WEB_SOURCES),
            Opportunity.status.not_in(("ganada", "perdida", "archivada")),
        ).order_by(Opportunity.id)
    ).all()
    if not filas:
        return 0

    hoy = local_date(datetime.now(UTC))
    e = html.escape
    items_html = "".join(
        f'<li><a href="{e(avisos.enlace(f"/oportunidades?id={o.id}"))}">'
        f"<b>{e(o.number)}</b></a> · {e(o.title[:80])} · "
        f"{(hoy - local_date(o.created_at.replace(tzinfo=UTC) if o.created_at and not o.created_at.tzinfo else o.created_at)).days if o.created_at else '?'} días sin responsable</li>"
        for o in filas[:20]
    )
    mas = f"<p>… y {len(filas) - 20} más.</p>" if len(filas) > 20 else ""
    cuerpo = (
        f"<p>Estas consultas de la tienda web llevan más de un día sin un vendedor responsable:</p>"
        f"<ul>{items_html}</ul>{mas}"
        + cta("/ventas/por-asignar", "Ir a Ventas por asignar")
    )
    destinos = miembros(db, tenant, roles=("admin",))
    n = avisos.avisar(db, tenant, destinos, f"{len(filas)} consulta{'s' if len(filas) != 1 else ''} sin responsable · tienda web", lambda _m: cuerpo, "opportunity", filas[0].id)
    return n


@shared_task(name="crm.stale_quotes")
def stale_quotes() -> int:
    """Lunes a viernes por la manana (beat "stale-quotes"): cotizaciones frias a su vendedora."""
    from ..core.db import SessionLocal
    from ..models import Tenant

    n = 0
    with SessionLocal() as db:
        for t in db.scalars(select(Tenant).where(Tenant.active)).all():
            try:
                n += avisar_cotizaciones_frias(db, t)
                db.commit()
            except Exception:  # noqa: BLE001  una empresa con datos raros no frena a las demas
                db.rollback()
                log.exception("cotizaciones frias (empresa %s)", t.id)
    return n


@shared_task(name="crm.consultas_sin_responsable")
def consultas_sin_responsable() -> int:
    """Correo diario al admin con las consultas de la tienda web sin vendedor asignado."""
    from ..core.db import SessionLocal
    from ..models import Tenant

    n = 0
    with SessionLocal() as db:
        for t in db.scalars(select(Tenant).where(Tenant.active)).all():
            try:
                n += avisar_consultas_sin_responsable(db, t)
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
                log.exception("consultas sin responsable (empresa %s)", t.id)
    return n


@shared_task(name="crm.agenda_diaria")
def agenda_diaria() -> int:
    """06:45 de lunes a sabado (beat "agenda-diaria"): a cada vendedor su agenda de hoy y lo atrasado (ventas etapa 3)."""
    from ..core.db import SessionLocal
    from ..models import Tenant
    from ..services import agenda

    n = 0
    with SessionLocal() as db:
        for t in db.scalars(select(Tenant).where(Tenant.active)).all():
            try:
                n += agenda.digest(db, t)
                db.commit()
            except Exception:  # noqa: BLE001  una empresa con datos raros no frena a las demas
                db.rollback()
                log.exception("agenda diaria (empresa %s)", t.id)
    return n
