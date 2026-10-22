"""SLA de soporte: cuanto tiempo hay para responder y para resolver un ticket segun su prioridad.

Cascada (la primera que exista manda):
1. Contrato de mantenimiento/soporte ACTIVO del cliente que traiga SLA propio (lo que se le vendio a ese cliente).
2. SLA del tenant en Ajustes (tenant.settings["sla"]), editable por quien tenga settings.configurar.
3. Los valores por defecto de aqui abajo.

Se guarda en el ticket una foto del SLA que aplico (ticket.sla) para que, si mañana cambian los ajustes,
el ticket siga midiendose contra lo que se prometio el dia que entro.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import MaintenanceContract, SupportTicket, Tenant

PRIORIDADES = ("critica", "alta", "media", "baja")
# Primera respuesta: el compromiso historico (2/4/8/24 h). Resolucion: SUPUESTO razonable para integracion de
# seguridad (critica en el dia, baja en una semana laboral); Andres los ajusta en Ajustes.
SLA_DEFECTO: dict[str, dict[str, int]] = {
    "critica": {"respuesta": 2, "resolucion": 8},
    "alta": {"respuesta": 4, "resolucion": 24},
    "media": {"respuesta": 8, "resolucion": 72},
    "baja": {"respuesta": 24, "resolucion": 120},
}
MAX_HORAS = 24 * 90  # nada razonable pasa de 90 dias; frena errores de digitacion


def normalizar(raw: dict | None) -> dict[str, dict[str, int]] | None:
    """Deja solo prioridades y horas validas. Devuelve None si no queda nada usable."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, dict[str, int]] = {}
    for pr in PRIORIDADES:
        v = raw.get(pr)
        if not isinstance(v, dict):
            continue
        try:
            resp, reso = int(v.get("respuesta")), int(v.get("resolucion"))
        except (TypeError, ValueError):
            continue
        if 0 < resp <= MAX_HORAS and 0 < reso <= MAX_HORAS:
            out[pr] = {"respuesta": resp, "resolucion": max(reso, resp)}  # no se puede resolver antes de responder
    return out or None


def sla_tenant(tenant: Tenant) -> dict[str, dict[str, int]]:
    propio = normalizar((tenant.settings or {}).get("sla")) or {}
    return {pr: propio.get(pr) or SLA_DEFECTO[pr] for pr in PRIORIDADES}


def contrato_vigente(db: Session, tenant_id: int, customer_id: int | None, contract_id: int | None = None) -> MaintenanceContract | None:
    """El contrato activo del cliente con SLA propio. Si el ticket ya viene de un contrato, ese primero."""
    if contract_id:
        c = db.get(MaintenanceContract, contract_id)
        if c and c.tenant_id == tenant_id and _vigente(c) and normalizar(c.sla):
            return c
    if not customer_id:
        return None
    for c in db.scalars(
        select(MaintenanceContract)
        .where(MaintenanceContract.tenant_id == tenant_id, MaintenanceContract.customer_id == customer_id, MaintenanceContract.active)
        .order_by(MaintenanceContract.id.desc())
    ):
        if _vigente(c) and normalizar(c.sla):
            return c
    return None


def _vigente(c: MaintenanceContract) -> bool:
    hoy = date.today()
    return c.active and (not c.start_date or c.start_date <= hoy) and (not c.end_date or c.end_date >= hoy)


def resolver(db: Session, tenant: Tenant, priority: str, customer_id: int | None, contract_id: int | None = None) -> dict:
    """Que SLA aplica y de donde salio: {respuesta, resolucion, origen, contrato}."""
    c = contrato_vigente(db, tenant.id, customer_id, contract_id)
    if c:
        horas = normalizar(c.sla).get(priority)
        if horas:
            return {**horas, "origen": "contrato", "contrato": c.number, "contrato_id": c.id}
    propio = normalizar((tenant.settings or {}).get("sla")) or {}
    if priority in propio:
        return {**propio[priority], "origen": "empresa", "contrato": None, "contrato_id": None}
    return {**SLA_DEFECTO.get(priority, SLA_DEFECTO["media"]), "origen": "defecto", "contrato": None, "contrato_id": None}


def aplicar(db: Session, tenant: Tenant, t: SupportTicket, desde: datetime | None = None) -> None:
    """Calcula due_at (primera respuesta) y resolve_due_at (resolucion) desde la creacion del ticket."""
    base = desde or _aware(t.created_at) or datetime.now(UTC)
    s = resolver(db, tenant, t.priority, t.customer_id, t.contract_id)
    t.sla = {**s, "prioridad": t.priority}
    t.due_at = base + timedelta(hours=s["respuesta"])
    t.resolve_due_at = base + timedelta(hours=s["resolucion"])


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


ABIERTOS = ("nuevo", "asignado", "en_proceso", "esperando_cliente")


# ---- Definicion UNICA de "abierto" y "fuera de tiempo" (inicio, lista de tickets y avisos usan esto) ----
def es_abierto(t: SupportTicket) -> bool:
    """Abierto = no resuelto ni cerrado."""
    return t.status in ABIERTOS


def vencimientos(t: SupportTicket, ahora: datetime | None = None) -> dict[str, bool]:
    """Que plazo del SLA se paso sin cumplirse. Solo cuenta en tickets abiertos."""
    ahora = ahora or datetime.now(UTC)
    if not es_abierto(t):
        return {"respuesta": False, "resolucion": False}
    return {
        "respuesta": bool(t.due_at and not t.first_reply_at and _aware(t.due_at) < ahora),
        "resolucion": bool(t.resolve_due_at and not t.resolved_at and _aware(t.resolve_due_at) < ahora),
    }


def fuera_de_tiempo(t: SupportTicket, ahora: datetime | None = None) -> bool:
    """Abierto y con algun plazo del SLA (respuesta o resolucion) vencido sin cumplir."""
    v = vencimientos(t, ahora)
    return v["respuesta"] or v["resolucion"]


# ---- Semaforo de la bandeja (una sola definicion: la lista, los filtros, el conteo y el orden usan esto) ----
UMBRAL_AMBAR = 0.25  # queda <= 25 % de la ventana del plazo -> "por vencer"
SIN_MOVIMIENTO_H = 48  # abierto y sin notas en 48 h -> "sin movimiento"
CR = timezone(timedelta(hours=-6))  # Costa Rica, sin horario de verano
FILTROS = ("vencidos", "por_vencer", "sin_responsable", "seguimiento_hoy", "sin_movimiento")
GRUPOS = ("vencidos", "por_vencer", "seguimiento_hoy", "esperando", "en_tiempo", "cerrados")  # orden de la bandeja


def fin_del_dia_cr(ahora: datetime) -> datetime:
    """23:59:59 de hoy en Costa Rica, en UTC."""
    local = (_aware(ahora) or datetime.now(UTC)).astimezone(CR)
    return local.replace(hour=23, minute=59, second=59, microsecond=0).astimezone(UTC)


def _minutos(delta: timedelta) -> int:
    """Minutos enteros redondeados hacia abajo (negativo si ya vencio)."""
    return math.floor(delta.total_seconds() / 60)


def semaforo(t: SupportTicket, ahora: datetime | None = None) -> dict:
    """{color, plazo, vence_at, ventana_min, restante_min}
    color: rojo | ambar | verde | gris | neutro
    - no abierto (resuelto/cerrado) -> neutro, todo None
    - plazo activo: "respuesta" (due_at) si no hay first_reply_at; si ya hubo respuesta, "resolucion" (resolve_due_at)
      si no hay resolved_at; si no hay plazo (ticket viejo o preventivo ya respondido) -> neutro con plazo None
    - fuera_de_tiempo(t) -> rojo (misma definicion que el inicio y el filtro "vencidos"); plazo = el vencido
    - status == esperando_cliente -> gris (el reloj NO se pausa: si vence pasa a rojo)
    - restante <= ventana * UMBRAL_AMBAR -> ambar   (ventana = vence_at - created_at)
    - si no -> verde"""
    ahora = _aware(ahora) or datetime.now(UTC)
    vacio = {"color": "neutro", "plazo": None, "vence_at": None, "ventana_min": None, "restante_min": None}
    if not es_abierto(t):
        return vacio
    venc = vencimientos(t, ahora)
    if venc["respuesta"]:
        plazo, vence = "respuesta", t.due_at
    elif venc["resolucion"]:
        plazo, vence = "resolucion", t.resolve_due_at
    elif t.due_at and not t.first_reply_at:
        plazo, vence = "respuesta", t.due_at
    elif t.resolve_due_at and not t.resolved_at:
        plazo, vence = "resolucion", t.resolve_due_at
    else:
        return vacio
    vence = _aware(vence)
    creado = _aware(t.created_at) or ahora
    ventana = max(_minutos(vence - creado), 0)
    restante = _minutos(vence - ahora)
    if venc["respuesta"] or venc["resolucion"]:
        color = "rojo"
    elif t.status == "esperando_cliente":
        color = "gris"
    elif restante <= ventana * UMBRAL_AMBAR:
        color = "ambar"
    else:
        color = "verde"
    return {"color": color, "plazo": plazo, "vence_at": vence, "ventana_min": ventana, "restante_min": restante}


def seguimiento_hoy(t: SupportTicket, ahora: datetime | None = None) -> bool:
    """Proximo paso para hoy (dia de Costa Rica) o ya atrasado."""
    ahora = _aware(ahora) or datetime.now(UTC)
    return bool(t.next_step_at) and _aware(t.next_step_at) <= fin_del_dia_cr(ahora)


def grupo(t: SupportTicket, ahora: datetime | None = None, sem: dict | None = None) -> str:
    """Grupo de la bandeja, uno solo por ticket, en este orden de precedencia:
    vencidos (rojo) > por_vencer (ambar) > seguimiento_hoy (next_step_at <= fin del dia CR, incluye atrasados)
    > esperando (gris) > en_tiempo (verde/neutro abiertos) > cerrados (resuelto/cerrado)."""
    if not es_abierto(t):
        return "cerrados"
    color = (sem or semaforo(t, ahora))["color"]
    if color == "rojo":
        return "vencidos"
    if color == "ambar":
        return "por_vencer"
    if seguimiento_hoy(t, ahora):
        return "seguimiento_hoy"
    if color == "gris":
        return "esperando"
    return "en_tiempo"


def ultimo_movimiento(t: SupportTicket) -> datetime:
    """created_at de la ultima nota (cualquier tipo) o, si no hay, created_at del ticket."""
    fechas = [_aware(n.created_at) for n in (t.notes or []) if n.created_at]
    return max(fechas) if fechas else (_aware(t.created_at) or datetime.now(UTC))


def cumple(t: SupportTicket, filtro: str, ahora: datetime | None = None) -> bool:
    """Filtros rapidos de la bandeja. Siempre sobre abiertos. La lista (?filtro=) y el conteo (meta.resumen) usan esto."""
    ahora = _aware(ahora) or datetime.now(UTC)
    if not es_abierto(t):
        return False
    if filtro == "vencidos":
        return fuera_de_tiempo(t, ahora)
    if filtro == "por_vencer":
        return semaforo(t, ahora)["color"] == "ambar"
    if filtro == "sin_responsable":
        return t.assigned_to is None
    if filtro == "seguimiento_hoy":
        return seguimiento_hoy(t, ahora)
    if filtro == "sin_movimiento":
        return ultimo_movimiento(t) < ahora - timedelta(hours=SIN_MOVIMIENTO_H)
    raise ValueError(filtro)


def resumen(tickets, ahora: datetime | None = None) -> dict[str, int]:
    ahora = ahora or datetime.now(UTC)
    abiertos = [t for t in tickets if es_abierto(t)]
    return {"abiertos": len(abiertos), "fuera_de_tiempo": sum(1 for t in abiertos if fuera_de_tiempo(t, ahora))}


def revisar_atrasos(db: Session, tenant: Tenant, ahora: datetime | None = None) -> dict[str, list[SupportTicket]]:
    """Tickets que se acaban de pasar de su plazo de primera respuesta o de resolucion.

    Cada ticket avisa UNA vez por plazo (se anota en ticket.sla["avisos"]); antes el worker repetia el mismo
    correo cada hora y eso termina en que nadie lo lee."""
    ahora = ahora or datetime.now(UTC)
    out: dict[str, list[SupportTicket]] = {"respuesta": [], "resolucion": []}
    # lo archivado o en la papelera no avisa
    vivos = (SupportTicket.archived_at.is_(None), SupportTicket.trashed_at.is_(None))
    for t in db.scalars(select(SupportTicket).where(SupportTicket.tenant_id == tenant.id, SupportTicket.status.in_(ABIERTOS), *vivos)):
        avisos = list((t.sla or {}).get("avisos") or [])
        nuevos = []
        if t.due_at and not t.first_reply_at and _aware(t.due_at) < ahora and "respuesta" not in avisos:
            nuevos.append("respuesta")
        if t.resolve_due_at and not t.resolved_at and _aware(t.resolve_due_at) < ahora and "resolucion" not in avisos:
            nuevos.append("resolucion")
        for k in nuevos:
            out[k].append(t)
        if nuevos:
            t.sla = {**(t.sla or {}), "avisos": avisos + nuevos}  # reasignar: el JSON no detecta mutaciones in-place
    return out


PRIORIDAD_TXT = {"critica": "crítica", "alta": "alta", "media": "media", "baja": "baja"}


def vencido_texto(delta: timedelta) -> str:
    """Cuanto lleva vencido, para leer: '25 min', '3 h 20 min', '2 días 4 h'."""
    mins = max(int(delta.total_seconds() // 60), 0)
    if mins < 60:
        return f"{mins} min"
    h, m = divmod(mins, 60)
    if h < 48:
        return f"{h} h" + (f" {m} min" if m else "")
    d, hh = divmod(h, 24)
    return f"{d} días" + (f" {hh} h" if hh else "")


def _cliente(db: Session, t: SupportTicket) -> str:
    """Nombre del cliente de la ficha o, si no hay ficha, de quien reporto."""
    from ..models import Customer

    c = db.get(Customer, t.customer_id) if t.customer_id else None
    if c is not None and c.name:
        return c.name
    contacto = t.contact or {}
    return (contacto.get("name") or contacto.get("company") or "").strip() or "Sin cliente"


def enlace_ticket(t: SupportTicket) -> str:
    """El ticket abierto en la bandeja del panel (Tickets abre el detalle con ?id=)."""
    from ..core.config import settings

    return f"{settings.public_base_url.rstrip('/')}/tickets?id={t.id}"


def detalle_atraso(db: Session, t: SupportTicket, tipo: str, ahora: datetime) -> dict:
    """Lo que dice el aviso de un ticket atrasado: cliente, asunto, desde cuando vencio y el enlace."""
    vence = _aware(t.due_at if tipo == "respuesta" else t.resolve_due_at) or ahora
    return {
        "numero": t.number,
        "cliente": _cliente(db, t),
        "asunto": t.subject,
        "prioridad": PRIORIDAD_TXT.get(t.priority, t.priority),
        "vencio": vence.astimezone(CR).strftime("%d/%m/%Y %H:%M"),
        "vencido": vencido_texto(ahora - vence),
        "enlace": enlace_ticket(t),
    }


def _fila_html(d: dict) -> str:
    import html

    e = html.escape
    return (
        f'<li style="margin-bottom:10px"><b>{e(d["numero"])}</b> · {e(d["cliente"])}<br>'
        f'Asunto: {e(d["asunto"])}<br>'
        f'Prioridad {e(d["prioridad"])} · venció el {e(d["vencio"])} · <b>lleva {e(d["vencido"])} vencido</b><br>'
        f'<a href="{e(d["enlace"])}">Abrir el ticket</a></li>'
    )


def avisar_atrasos(db: Session, tenant: Tenant, ahora: datetime | None = None) -> int:
    """Manda los avisos de atraso por el mecanismo de siempre (outbox de correo). Devuelve cuantos tickets avisaron.

    Cada aviso dice de quien es el ticket (cliente), el asunto, cuanto lleva vencido y el enlace para abrirlo (antes
    llegaba casi vacio: solo el numero y el asunto). Es un aviso interno con datos del cliente: no va en copia a la
    lista de la empresa de Ajustes (company_bcc=False), igual que los demas avisos internos."""
    from ..models import User
    from .email_layout import cta
    from .mail import notify_roles, queue_email, user_inbox

    ahora = _aware(ahora) or datetime.now(UTC)
    titulos = {"respuesta": "sin primera respuesta a tiempo", "resolucion": "con la resolucion atrasada"}
    plazos = {"respuesta": "la primera respuesta", "resolucion": "la resolución"}
    n = 0
    for tipo, lista in revisar_atrasos(db, tenant, ahora).items():
        if not lista:
            continue
        detalles = [(x, detalle_atraso(db, x, tipo, ahora)) for x in lista]
        filas = "".join(_fila_html(d) for _, d in detalles[:20])
        mas = f"<p>… y {len(lista) - 20} más en Tickets.</p>" if len(lista) > 20 else ""
        intro = f"<p>Se venció el plazo de {plazos[tipo]} de {'este ticket' if len(lista) == 1 else f'{len(lista)} tickets'}:</p>"
        asunto = (
            f"Ticket {detalles[0][1]['numero']} {titulos[tipo]} · {detalles[0][1]['cliente']}"
            if len(lista) == 1
            else f"{len(lista)} ticket(s) {titulos[tipo]}"
        )
        notify_roles(db, tenant, ("admin", "supervisor"), asunto, f"{intro}<ul>{filas}</ul>{mas}" + cta("/tickets", "Abrir Tickets"), "support_ticket", lista[0].id, company_bcc=False)
        for x, d in detalles:
            u = db.get(User, x.assigned_to) if x.assigned_to else None
            correo = user_inbox(db, tenant.id, u) if u else ""
            if correo:
                cuerpo = f"<p>Se venció el plazo de {plazos[tipo]} de un ticket asignado a usted:</p><ul>{_fila_html(d)}</ul>" + cta(f"/tickets?id={x.id}", f"Abrir el ticket {x.number}")
                queue_email(db, tenant, correo, f"Ticket {x.number} {titulos[tipo]} · {d['cliente']}", cuerpo, "support_ticket", x.id, company_bcc=False)
        n += len(lista)
    return n
