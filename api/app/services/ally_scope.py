"""Alcance central del portal de aliados: lo que una empresa aliada tiene EN COMUN con Crimson (deny by default).

Todo lo que sale por /aliado/* pasa por aqui (routers/ally_portal.py): o select(...).where(*scope.X()) o scope.one().
Nada interno gana filtros "en comun": los endpoints internos siguen cerrados al aliado (get_principal).

En comun =
- oportunidades: con una participacion de ESTA empresa visible para el aliado (visible_to_ally);
- proyectos: los de esas participaciones (la participacion queda ligada al proyecto al crearlo);
- levantamientos, ordenes y tickets: los asignados explicitamente a la empresa (ally_company_id);
- contratos: asignados a la empresa o de un proyecto en comun (17:27 "lo que ya hemos instalado en comun");
- activos: de proyectos en comun (por defecto) o de clientes en comun (tenant.settings.ally_assets_scope).
El tecnico del aliado (tecnico_aliado) solo ve lo suyo de HOY: nada de oportunidades, proyectos, contratos ni activos.
Lo de otra empresa o interno responde 404 ("... no encontrado"), sin confirmar que existe.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import false, or_, select, union
from sqlalchemy.orm import Session

from ..core.deps import Principal
from ..models import (
    AllyCompany,
    AllyParticipation,
    CustomerAsset,
    MaintenanceContract,
    Opportunity,
    Project,
    SupportTicket,
    Survey,
    TenantUser,
    User,
    WorkOrder,
)
from .archive import live
from .documents import local_date

OPEN_ORDER = ("asignada", "en_sitio", "en_proceso")


class AllyScope:
    def __init__(self, db: Session, p: Principal, company: AllyCompany):
        self.db, self.tid, self.cid, self.uid = db, p.tenant.id, company.id, p.user.id
        self.is_tech = p.role == "tecnico_aliado"
        self.today = local_date(datetime.now(UTC))
        self.assets_mode = (p.tenant.settings or {}).get("ally_assets_scope", "proyectos")
        self._team: set[int] | None = None

    # ---------- subconsultas (Select), nunca listas en memoria ----------
    def participations(self):
        return select(AllyParticipation).where(
            AllyParticipation.tenant_id == self.tid, AllyParticipation.ally_company_id == self.cid, AllyParticipation.visible_to_ally.is_(True)
        )

    def opp_ids(self):
        return select(AllyParticipation.opportunity_id).where(
            AllyParticipation.tenant_id == self.tid, AllyParticipation.ally_company_id == self.cid, AllyParticipation.visible_to_ally.is_(True)
        )

    def project_ids(self):
        return select(AllyParticipation.project_id).where(
            AllyParticipation.tenant_id == self.tid,
            AllyParticipation.ally_company_id == self.cid,
            AllyParticipation.visible_to_ally.is_(True),
            AllyParticipation.project_id.is_not(None),
        )

    def team_user_ids(self) -> set[int]:
        """Toda la gente de la empresa aliada (activa o no: la autoria de notas y fotos sigue siendo suya)."""
        if self._team is None:
            self._team = set(self.db.scalars(select(TenantUser.user_id).where(TenantUser.tenant_id == self.tid, TenantUser.ally_company_id == self.cid)))
        return self._team

    def active_team(self) -> list[TenantUser]:
        return list(
            self.db.scalars(
                select(TenantUser)
                .join(User, User.id == TenantUser.user_id)
                .where(TenantUser.tenant_id == self.tid, TenantUser.ally_company_id == self.cid, TenantUser.active, User.active)
            )
        )

    def customer_ids(self):
        """Clientes en comun: contratante y cliente final de oportunidades y proyectos en comun (sin NULL)."""
        opps = select(Opportunity.id).where(Opportunity.tenant_id == self.tid, Opportunity.id.in_(self.opp_ids()), live(Opportunity))
        prjs = select(Project.id).where(Project.tenant_id == self.tid, Project.id.in_(self.project_ids()), live(Project))
        u = union(
            select(Opportunity.customer_id).where(Opportunity.id.in_(opps), Opportunity.customer_id.is_not(None)),
            select(Opportunity.end_customer_id).where(Opportunity.id.in_(opps), Opportunity.end_customer_id.is_not(None)),
            select(Project.customer_id).where(Project.id.in_(prjs), Project.customer_id.is_not(None)),
            select(Project.end_customer_id).where(Project.id.in_(prjs), Project.end_customer_id.is_not(None)),
        )
        return select(u.subquery().c[0])

    # ---------- condiciones para .where(*conds); [false()] donde el tecnico no entra ----------
    def opportunities(self) -> list:
        if self.is_tech:
            return [false()]
        return [Opportunity.tenant_id == self.tid, Opportunity.id.in_(self.opp_ids()), live(Opportunity)]

    def projects(self) -> list:
        if self.is_tech:
            return [false()]
        return [Project.tenant_id == self.tid, Project.id.in_(self.project_ids()), live(Project)]

    def surveys(self) -> list:
        conds = [Survey.tenant_id == self.tid, Survey.ally_company_id == self.cid, live(Survey)]
        if self.is_tech:
            conds += [Survey.technician_id == self.uid, or_(Survey.status == "borrador", Survey.review_status == "devuelto")]
        return conds

    def work_orders(self) -> list:
        """El tecnico ademas filtra con is_today_for_tech en Python (sin limit antes del filtro)."""
        from ..routers.fieldwork import _live_orders

        return [WorkOrder.tenant_id == self.tid, WorkOrder.ally_company_id == self.cid, *_live_orders()]

    def tickets(self) -> list:
        conds = [SupportTicket.tenant_id == self.tid, SupportTicket.ally_company_id == self.cid, live(SupportTicket)]
        if self.is_tech:
            # abiertos asignados a el; los "resuelto" solo si se resolvieron hoy (eso se mira en Python)
            conds += [SupportTicket.assigned_to == self.uid, SupportTicket.status != "cerrado"]
        return conds

    def contracts(self) -> list:
        if self.is_tech:
            return [false()]
        return [
            MaintenanceContract.tenant_id == self.tid,
            live(MaintenanceContract),
            or_(MaintenanceContract.ally_company_id == self.cid, MaintenanceContract.project_id.in_(self.project_ids())),
        ]

    def assets(self) -> list:
        if self.is_tech:
            return [false()]
        if self.assets_mode == "clientes":
            cond = CustomerAsset.customer_id.in_(self.customer_ids())
        else:
            cond = CustomerAsset.project_id.in_(self.project_ids())
        return [CustomerAsset.tenant_id == self.tid, live(CustomerAsset), cond]

    # ---------- reglas en Python ----------
    def is_today_for_tech(self, o: WorkOrder) -> bool:
        asignado = o.technician_id == self.uid or self.uid in (o.helpers or [])
        abierta = o.status in OPEN_ORDER and (o.scheduled_at is None or local_date(o.scheduled_at) <= self.today)
        hoy = o.status == "finalizada" and o.finished_at is not None and local_date(o.finished_at) == self.today
        return bool(asignado and (abierta or hoy))

    def ticket_visible(self, t: SupportTicket) -> bool:
        """El tecnico: un resuelto solo el dia que se resolvio (en la lista de hoy)."""
        if not self.is_tech or t.status != "resuelto":
            return True
        return t.resolved_at is not None and local_date(t.resolved_at) == self.today

    def one(self, model, oid: int, conds: list, label: str):
        obj = self.db.scalar(select(model).where(model.id == oid, *conds))
        if obj is None:
            raise HTTPException(404, f"{label} no encontrado")
        return obj


def check_ally_assignment(
    db: Session,
    tenant_id: int,
    *,
    ally_company_id: int | None,
    user_ids: list[int | None],
    project_id: int | None = None,
    opportunity_id: int | None = None,
    current: int | None = None,
    existing=(),
) -> int | None:
    """Escrituras INTERNAS (OT, levantamiento, ticket, contrato): la empresa aliada a la que queda asignado el trabajo.

    Los tecnicos aliados de la lista tienen que ser de una sola empresa, la misma que la explicita si viene; si no viene,
    se toma la de los tecnicos. Con proyecto u oportunidad, la empresa tiene que participar ahi. La empresa tiene que
    estar activa y sin suspender (salvo que sea la que el trabajo YA tenia, current: editar una orden o un ticket de un
    aliado suspendido no se traba; asignarle trabajo nuevo si). Solo se exige activo a quien ENTRA: los de existing (el
    equipo o el responsable que el trabajo ya tenia) se conservan aunque hoy esten desactivados, como en
    fieldwork._check_crew; la empresa unica y la participacion se validan para todos. Devuelve el id final o None
    (trabajo de Crimson)."""
    ids = [u for u in dict.fromkeys(user_ids) if u]
    externos = (
        list(db.scalars(select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.user_id.in_(ids), TenantUser.ally_company_id.is_not(None))))
        if ids
        else []
    )
    empresas = {m.ally_company_id for m in externos}
    if len(empresas) > 1:
        raise HTTPException(422, "Un trabajo no puede tener técnicos de dos empresas aliadas")
    if ally_company_id is not None and empresas and empresas != {ally_company_id}:
        raise HTTPException(422, "Ese técnico es de otra empresa aliada")
    cid = ally_company_id if ally_company_id is not None else (next(iter(empresas)) if empresas else None)
    if cid is None:
        return None
    company = db.get(AllyCompany, cid)
    if not company or company.tenant_id != tenant_id:
        raise HTTPException(404, "Empresa aliada no encontrada")
    if (not company.active or company.suspended_at) and cid != current:
        raise HTTPException(422, "Esa empresa aliada está suspendida o inactiva")
    ya_estaban = {i for i in existing or () if i}
    for m in externos:
        if m.user_id not in ya_estaban and (not m.active or not m.user.active):
            raise HTTPException(422, "Ese técnico está desactivado")
    if project_id is not None or opportunity_id is not None:
        q = select(AllyParticipation.id).where(AllyParticipation.tenant_id == tenant_id, AllyParticipation.ally_company_id == cid)
        q = q.where(AllyParticipation.project_id == project_id) if project_id is not None else q.where(AllyParticipation.opportunity_id == opportunity_id)
        if db.scalar(q.limit(1)) is None:
            raise HTTPException(422, f"{company.name} no participa en {'este proyecto' if project_id is not None else 'esta oportunidad'}")
    return cid


AVISO_TRABAJO_MIN = 30  # no se repite el aviso del mismo trabajo a la misma persona antes de esto


def _ya_avisado(db: Session, tenant_id: int, correo: str, number: str) -> bool:
    """Ya se le aviso a esa persona de ESE trabajo hace poco: alternar el tecnico (A -> B -> A) o quitar y volver a poner
    un ayudante no encola correos sin tope (como support_desk._ya_avisado)."""
    from datetime import timedelta

    from ..models import EmailOutbox

    desde = datetime.now(UTC) - timedelta(minutes=AVISO_TRABAJO_MIN)
    return (
        db.scalar(
            select(EmailOutbox.id)
            .where(
                EmailOutbox.tenant_id == tenant_id,
                EmailOutbox.entity == "ally_work",
                EmailOutbox.to == correo,
                EmailOutbox.subject.startswith(_asunto_prefijo(number), autoescape=True),
                EmailOutbox.created_at >= desde,
            )
            .limit(1)
        )
        is not None
    )


def _asunto_prefijo(number: str) -> str:
    return f"Tiene un trabajo nuevo: {number} · "


def notify_ally_work(db: Session, tenant, user_ids, number: str, title: str, when=None, entity_id: int | None = None) -> int:
    """Aviso al usuario de una empresa aliada al que se le asigna un trabajo (OT, ticket, levantamiento). Sin montos.
    A los internos no se les escribe desde aqui (tienen sus propios avisos). Un aviso por persona y trabajo cada
    AVISO_TRABAJO_MIN minutos (_ya_avisado); entity_id es el id del trabajo (el numero lo distingue entre tipos)."""
    import html as _html
    from datetime import date as _date

    from ..core.config import settings as cfg
    from .mail import queue_email

    ids = {i for i in user_ids or [] if i}
    if not ids:
        return 0
    rows = db.scalars(
        select(TenantUser).where(TenantUser.tenant_id == tenant.id, TenantUser.user_id.in_(ids), TenantUser.ally_company_id.is_not(None), TenantUser.active)
    ).all()
    if isinstance(when, datetime):
        fecha = local_date(when).strftime("%d/%m/%Y")
    elif isinstance(when, _date):
        fecha = when.strftime("%d/%m/%Y")
    else:
        fecha = "sin fecha"
    base = cfg.public_base_url.rstrip("/")
    n = 0
    for m in rows:
        u = m.user
        if not u or not u.active or not u.email:
            continue
        if _ya_avisado(db, tenant.id, u.email, number):
            continue
        queue_email(
            db,
            tenant,
            u.email,
            f"{_asunto_prefijo(number)}{title[:80]} · {fecha}",
            f"<p>{_html.escape(tenant.name)} le asignó <b>{_html.escape(number)}</b> · {_html.escape(title)} · {fecha}.</p>"
            f'<p><a href="{base}/">Abrir el portal de aliados</a></p>',
            "ally_work",
            entity_id,
        )
        n += 1
    return n
