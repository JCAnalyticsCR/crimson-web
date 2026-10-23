"""Avisos que destraban la venta (plan maestro 1.6).

Cada evento que frena dinero le llega por correo, con enlace directo al panel (PUBLIC_BASE_URL), a quien lo tiene que
mover (la RACI de avisos de la sesion con Andres):

- Cotizacion aceptada por el cliente -> administracion, el supervisor y la vendedora (plan 1.6). En una venta de solo
  equipo el supervisor no tiene instalacion que programar: Ajustes -> Notificaciones lo puede apagar
  (notify_supervisor_equipment_sale; por defecto encendido, lo que dice el plan, hasta que la RACI de la sesion 0.11
  decida otra cosa).
- Cotizacion retenida por descuento, monto o margen -> quien aprueba (sales.aprobar); aprobada o rechazada -> la
  vendedora, si todavia la puede abrir (si no, administracion, para reasignarla).
- Orden de trabajo asignada a alguien de Crimson -> esa persona (los de una empresa aliada: ally_scope.notify_ally_work).
- Precio recibido de un aliado -> quien lo aprueba y ve costos, no la vendedora (lo que cobra el aliado es costo).
- Cotizaciones frias -> la vendedora de cada una (app/tasks/ventas.py).

Son correos internos: nunca van en copia al BCC de la empresa (Ajustes -> "Correos en copia (BCC) de toda factura" suele
ser un buzon de archivo o el contador externo; queue_email(company_bcc=False)). Sin costos ni margenes en el cuerpo y,
salvo el resumen de frias (su total, solo a quien ve los precios de esa cotizacion), sin montos: el motivo de una
retenida va sin el monto ni el tope (_motivo_sin_cifras). Fatiga de alertas: quien hizo el cambio no se avisa a si mismo, cada persona recibe un solo correo por evento
y el mismo aviso no se repite dentro de su ventana (ya_avisado, como support_desk._ya_avisado y
ally_scope._ya_avisado). Cada enlace lleva a una pantalla que esa persona puede abrir: nunca a un 404."""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.deps import EXTERNAL_ROLES, _role_permissions, effective_permissions, roles_like
from ..models import Customer, EmailOutbox, Opportunity, Quote, Survey, TenantUser, User
from .documents import local_date
from .mail import internal_inboxes, member_inbox, queue_email

# ventanas en que el mismo aviso a la misma persona no se repite (minutos)
VENTANA_ACEPTADA = 24 * 60  # deshacer y volver a registrar la aceptacion el mismo dia no avisa dos veces
VENTANA_POR_APROBAR = 30  # pasarse del limite, corregir y volver a pasarse no inunda a quien aprueba
VENTANA_TRABAJO = 30  # alternar el tecnico (A -> B -> A) no encola correos sin tope (como notify_ally_work)
VENTANA_PRECIO_ALIADO = 30  # varios precios seguidos del mismo aliado en la misma oportunidad: un aviso


def enlace(path: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}{path}"


@dataclass(frozen=True)
class Miembro:
    """Usuario interno activo de la empresa con los permisos efectivos de su rol (incluye Ajustes -> Quien ve costos)."""

    user: User
    role: str
    perms: dict
    # a donde le llegan los avisos: su correo de avisos (Ajustes -> Usuarios, pedido 22) o, sin el, el de entrada
    inbox: str = ""

    def can(self, module: str, action: str) -> bool:
        if "*" in self.perms:
            return True
        acts = self.perms.get(module, [])
        return "*" in acts or action in acts


def miembros(db: Session, tenant, *, roles: Iterable[str] | None = None, user_ids: Iterable[int | None] | None = None) -> list[Miembro]:
    """Equipo interno activo (ni clientes, ni aliados, ni el acceso temporal de soporte), opcionalmente filtrado por rol
    o por usuario. Un usuario desactivado o una membresia inactiva no recibe nada."""
    stmt = (
        select(TenantUser)
        .join(User, User.id == TenantUser.user_id)
        .where(
            TenantUser.tenant_id == tenant.id,
            TenantUser.active,
            User.active,
            TenantUser.customer_id.is_(None),
            TenantUser.ally_company_id.is_(None),
            TenantUser.role_code.not_in((*EXTERNAL_ROLES, "soporte")),
        )
    )
    if roles is not None:
        stmt = stmt.where(TenantUser.role_code.in_(tuple(roles)))
    if user_ids is not None:
        ids = {i for i in user_ids if i}
        if not ids:
            return []
        stmt = stmt.where(TenantUser.user_id.in_(ids))
    cache: dict[str, dict] = {}
    out = []
    for m in db.scalars(stmt.order_by(TenantUser.id)):
        if m.role_code not in cache:
            cache[m.role_code] = effective_permissions(tenant, m.role_code, _role_permissions(db, m.role_code))
        out.append(Miembro(m.user, m.role_code, cache[m.role_code], member_inbox(m)))
    return out


def ya_avisado(db: Session, tenant_id: int, correo: str, entity: str, entity_id: int | None, asunto: str, minutos: int) -> bool:
    """El mismo aviso (mismo asunto, mismo registro) ya le salio a esa persona dentro de la ventana."""
    if minutos <= 0:
        return False
    desde = datetime.now(UTC) - timedelta(minutes=minutos)
    return (
        db.scalar(
            select(EmailOutbox.id)
            .where(
                EmailOutbox.tenant_id == tenant_id,
                EmailOutbox.entity == entity,
                EmailOutbox.entity_id == entity_id,
                EmailOutbox.to == correo,
                EmailOutbox.subject == asunto,
                EmailOutbox.created_at >= desde,
            )
            .limit(1)
        )
        is not None
    )


def avisar(
    db: Session,
    tenant,
    destinos: Iterable[Miembro],
    asunto: str,
    cuerpo: Callable[[Miembro], str],
    entity: str,
    entity_id: int | None,
    *,
    minutos: int = 0,
    excluir: int | None = None,
) -> int:
    """Un correo por persona (sin repetir correo), salvo a quien hizo el cambio y a quien ya lo recibio en la ventana.
    Devuelve cuantos se encolaron."""
    asunto = asunto[:300]
    vistos: set[str] = set()
    n = 0
    for m in destinos:
        correo = (m.inbox or m.user.email or "").strip().lower()
        if not correo or correo in vistos or (excluir is not None and m.user.id == excluir):
            continue
        vistos.add(correo)
        if ya_avisado(db, tenant.id, correo, entity, entity_id, asunto, minutos):
            continue
        queue_email(db, tenant, correo, asunto, cuerpo(m), entity, entity_id, company_bcc=False)
        n += 1
    return n


def _html(*parrafos: str, url: str, texto: str) -> str:
    """Cuerpo comun: parrafos ya escapados y la llamada al panel. queue_email lo pone en el diseno base de los correos
    (email_layout.ensure): el asunto como titulo y el parrafo del enlace como el boton principal."""
    cuerpo = "".join(f"<p>{x}</p>" for x in parrafos if x)
    return f'{cuerpo}<p><a href="{html.escape(url)}">{html.escape(texto)}</a></p>'


def _e(v) -> str:
    return html.escape(str(v or ""))


# ---------- Cotizaciones ----------
def quote_owner(q: Quote) -> int | None:
    """La vendedora de la cotizacion: su vendedor responsable o, sin vendedor, quien la tecleo (services/ownership)."""
    return q.seller_id if q.seller_id is not None else q.created_by


def ve_cotizacion(m: Miembro, q: Quote) -> bool:
    """La misma regla que ownership.owns_doc: con sales.ver_todo todas; si no, solo la suya."""
    return m.can("sales", "ver") and (m.can("sales", "ver_todo") or m.user.id == quote_owner(q))


def _cliente(db: Session, q: Quote) -> str:
    c = db.get(Customer, q.customer_id) if q.customer_id else None
    return c.name if c is not None and c.tenant_id == q.tenant_id else "sin cliente"


def _lleva_instalacion(db: Session, q: Quote) -> tuple[bool, Opportunity | None]:
    """Una oportunidad de "venta" (solo equipo) no lleva instalacion: el supervisor no tiene nada que programar."""
    opp = db.scalar(select(Opportunity).where(Opportunity.tenant_id == q.tenant_id, Opportunity.quote_id == q.id).limit(1))
    return (opp is None or (opp.kind or "proyecto") != "venta"), opp


def supervisor_en_venta_de_equipo(tenant) -> bool:
    """Ajustes -> Notificaciones: la aceptada de una venta de solo equipo tambien le llega al supervisor (por defecto si)."""
    return bool((tenant.settings or {}).get("notify_supervisor_equipment_sale", True))


def cotizacion_aceptada(db: Session, tenant, q: Quote, *, version: int | None, quien: str | None, medio: str | None, actor_id: int | None) -> int:
    """El cliente dijo que si: administracion, el supervisor y la vendedora. Sin montos. En una venta de solo equipo el
    supervisor queda fuera solo si la empresa lo apago en Ajustes (supervisor_en_venta_de_equipo)."""
    instalacion, _opp = _lleva_instalacion(db, q)
    roles = ("admin", "supervisor") if instalacion or supervisor_en_venta_de_equipo(tenant) else ("admin",)
    # tambien los roles creados a partir de esos (un «Supervisor senior»; una copia del admin no: deps.roles_like)
    destinos = miembros(db, tenant, roles=roles_like(db, tenant, roles)) + miembros(db, tenant, user_ids=[quote_owner(q)])
    lev = db.scalar(select(Survey.id).where(Survey.tenant_id == q.tenant_id, Survey.quote_id == q.id).limit(1))
    cliente = _cliente(db, q)
    siguiente = (
        "conviértala a proyecto (o a factura) para programar la instalación y arrancar el cobro"
        if instalacion
        else "conviértala a factura para cobrarla y despachar el equipo"
    )

    def cuerpo(m: Miembro) -> str:
        if ve_cotizacion(m, q):
            url, texto = enlace(f"/cotizaciones/{q.id}"), f"Abrir {q.number}"
        elif lev and m.can("field", "ver"):
            url, texto = enlace(f"/levantamientos?id={lev}"), "Ver el levantamiento"
        elif m.can("projects", "ver"):
            url, texto = enlace("/proyectos"), "Ir a Proyectos"
        else:
            url, texto = enlace("/"), "Abrir el panel"
        detalle = " · ".join(
            x for x in (f"versión {version}" if version else "", f"por {_e(medio)}" if medio else "", f"respondió {_e(quien)}" if quien else "") if x
        )
        return _html(
            f"<b>{_e(cliente)}</b> aceptó la cotización <b>{_e(q.number)}</b>{f' ({detalle})' if detalle else ''}.",
            f"Siguiente paso: {siguiente}.",
            "Mientras no se convierta, queda en Aprobaciones → Aceptadas por convertir." if m.can("approvals", "ver") and m.can("sales", "crear") else "",
            url=url,
            texto=texto,
        )

    asunto = f"Cotización aceptada: {q.number} · {cliente}"
    n = avisar(db, tenant, destinos, asunto, cuerpo, "quote", q.id, minutos=VENTANA_ACEPTADA, excluir=actor_id)
    # el buzon interno de ventas (Ajustes -> "Correo para avisos internos", pedido 48) tambien se entera de la venta,
    # con el enlace a la cotizacion (lo abre quien entre con permiso) y la misma ventana: no se repite el mismo dia
    vistos = {(m.inbox or m.user.email or "").strip().lower() for m in destinos}
    cuerpo_buzon = _html(
        f"<b>{_e(cliente)}</b> aceptó la cotización <b>{_e(q.number)}</b>.", f"Siguiente paso: {siguiente}.", url=enlace(f"/cotizaciones/{q.id}"), texto=f"Abrir {q.number}"
    )
    for correo in internal_inboxes(tenant):
        if correo in vistos or ya_avisado(db, tenant.id, correo, "quote", q.id, asunto[:300], VENTANA_ACEPTADA):
            continue
        queue_email(db, tenant, correo, asunto[:300], cuerpo_buzon, "quote", q.id, company_bcc=False)
        n += 1
    return n


# la frase del tope de discounts.approval_reasons, con el total de la cotizacion y el tope interno de la empresa
# ("para vender" desde el 22/10; "para cotizar" en los motivos guardados antes)
_MONTO_TOPE = re.compile(r"El monto \([^)]*\) supera el tope de [\d.,]+ para (?:cotizar|vender) sin aprobación\.")


def _motivo_sin_cifras(texto: str | None) -> str | None:
    """El motivo para el correo: sin cifras de margen (public_reason) y sin el monto ni el tope (se ven en el panel)."""
    from .discounts import public_reason

    motivo = public_reason(texto, False)
    return _MONTO_TOPE.sub("El monto supera el tope para vender sin aprobación.", motivo) if motivo else motivo


def descuento_por_aprobar(db: Session, tenant, q: Quote, actor_id: int | None, *, tras_decision: bool = False) -> int:
    """La cotizacion quedo retenida (descuento, monto o margen): aviso inmediato a quien aprueba. El motivo va sin cifras
    de margen ni el monto y el tope (_motivo_sin_cifras): el correo no es lugar para el costo ni para los totales.
    Pasarse del limite, corregir y volver a pasarse no
    repite el aviso dentro de la ventana; volver a pedirlo despues de un rechazo o de una aprobacion si avisa (entre uno
    y otro hubo una decision de quien aprueba, no hay forma de inundarlo)."""
    destinos = [m for m in miembros(db, tenant) if m.can("sales", "aprobar")]
    if not destinos:
        return 0
    cliente = _cliente(db, q)
    vendedora = db.get(User, actor_id) if actor_id else None
    motivo = _motivo_sin_cifras(q.approval_reason)

    def cuerpo(m: Miembro) -> str:
        url, texto = (enlace(f"/cotizaciones/{q.id}"), "Revisar y aprobar") if ve_cotizacion(m, q) else (enlace("/aprobaciones"), "Abrir Aprobaciones")
        return _html(
            f"{_e(vendedora.full_name if vendedora else 'Alguien del equipo')} pide su aprobación para la cotización <b>{_e(q.number)}</b> de {_e(cliente)}.",
            f"Motivo: {_e(motivo)}" if motivo else "",
            "No se puede enviar ni convertir hasta que la apruebe o la rechace con un motivo.",
            url=url,
            texto=texto,
        )

    asunto = f"Cotización por aprobar: {q.number} · {cliente}"
    return avisar(db, tenant, destinos, asunto, cuerpo, "quote", q.id, minutos=0 if tras_decision else VENTANA_POR_APROBAR, excluir=actor_id)


def descuento_resuelto(db: Session, tenant, q: Quote, actor_id: int | None, *, aprobado: bool, motivo: str | None = None) -> int:
    """Quien aprueba decidio: la vendedora se entera en el momento, con el motivo si fue un no. Si ya no la puede abrir
    (inactiva, o con un rol que no ve esa cotizacion), le llega a administracion para reasignarla: el enlace nunca es
    un 404."""
    destinos = [m for m in miembros(db, tenant, user_ids=[quote_owner(q)]) if ve_cotizacion(m, q)]
    sin_duena = not destinos
    if sin_duena:
        destinos = miembros(db, tenant, roles=("admin",))
    quien = db.get(User, actor_id) if actor_id else None
    nombre = _e(quien.full_name if quien else "Administración")
    reasignar = "Su vendedora ya no la puede abrir: reasígnela para que le dé seguimiento." if sin_duena else ""
    if aprobado:
        asunto = f"Cotización aprobada: {q.number}"
        parrafos = (f"{nombre} aprobó la cotización <b>{_e(q.number)}</b>.", reasignar or "Ya la puede enviar al cliente o convertir.")
    else:
        asunto = f"Cotización rechazada: {q.number}"
        parrafos = (
            f"{nombre} rechazó el descuento de la cotización <b>{_e(q.number)}</b>.",
            f"Motivo: {_e(motivo)}" if motivo else "",
            reasignar or "Ajuste el descuento o los precios y guárdela: si queda dentro de su límite se libera; si no, vuelve a aprobación.",
        )

    def cuerpo(_m: Miembro) -> str:
        return _html(*parrafos, url=enlace(f"/cotizaciones/{q.id}"), texto=f"Abrir {q.number}")

    return avisar(db, tenant, destinos, asunto, cuerpo, "quote", q.id, excluir=actor_id)


# ---------- Ordenes de trabajo ----------
def ot_asignada(db: Session, tenant, o, user_ids: Iterable[int | None], actor_id: int | None) -> int:
    """Orden asignada a alguien de Crimson (tecnico o ayudante): la ve en "Mi dia", pero el correo le dice que existe.
    Los usuarios de una empresa aliada quedan fuera (miembros solo devuelve internos): los avisa notify_ally_work."""
    destinos = miembros(db, tenant, user_ids=user_ids)
    if not destinos:
        return 0
    c = db.get(Customer, o.customer_id) if o.customer_id else None
    cliente = c.name if c is not None and c.tenant_id == o.tenant_id else None
    fecha = local_date(o.scheduled_at).strftime("%d/%m/%Y") if o.scheduled_at else "por programar"

    def cuerpo(_m: Miembro) -> str:
        datos = "<br>".join(x for x in (f"Cliente: {_e(cliente)}" if cliente else "", f"Sitio: {_e(o.site)}" if o.site else "", f"Fecha: {fecha}") if x)
        return _html(
            f"Se le asignó la orden de trabajo <b>{_e(o.number)}</b> · {_e(o.title)}.",
            datos,
            url=enlace(f"/ordenes-trabajo?id={o.id}"),
            texto=f"Abrir {o.number}",
        )

    return avisar(
        db, tenant, destinos, f"Orden de trabajo asignada: {o.number} · {o.title[:80]}", cuerpo, "work_order", o.id, minutos=VENTANA_TRABAJO, excluir=actor_id
    )


# ---------- Aliados ----------
def precio_aliado(db: Session, tenant, o: Opportunity, r, empresa: str, texto: str) -> int:
    """Un aliado mando o respondio un precio: le llega a quien lo aprueba y ve costos (la misma regla que la seccion
    "Costos de aliados recibidos" de Aprobaciones: catalog.costos y crm_pipeline.editar, con todo el embudo o siendo el
    dueno o responsable). La vendedora ya no recibe el aviso. Sin montos."""
    cerca = {o.owner_id, getattr(r, "responsible_id", None)}
    destinos = [
        m
        for m in miembros(db, tenant)
        if m.can("catalog", "costos") and m.can("crm_pipeline", "editar") and (m.can("crm_pipeline", "ver_todo") or m.user.id in cerca)
    ]
    if not destinos:
        destinos = miembros(db, tenant, roles=("admin",))

    def cuerpo(m: Miembro) -> str:
        abre = m.can("crm_pipeline", "ver_todo") or m.user.id == o.owner_id
        url, boton = (enlace(f"/oportunidades?id={o.id}"), "Abrir la oportunidad") if abre else (enlace("/aprobaciones"), "Abrir Aprobaciones")
        return _html(
            f"<b>{_e(empresa)}</b> {_e(texto)} en <b>{_e(o.number)}</b> · {_e(o.title)}.",
            "Apruébelo o rechácelo en la oportunidad; también está en Aprobaciones → Costos de aliados recibidos.",
            url=url,
            texto=boton,
        )

    return avisar(
        db, tenant, destinos, f"{empresa} · precio recibido · {o.number} · {o.title[:60]}", cuerpo, "opportunity", o.id, minutos=VENTANA_PRECIO_ALIADO
    )
