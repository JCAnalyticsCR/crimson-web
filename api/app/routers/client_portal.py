"""Portal del cliente: lo que ve un usuario de una empresa cliente (roles cliente_admin y cliente_usuario).

Aislamiento (la regla que no se negocia):
- El cliente sale SIEMPRE de la membresia del usuario en el servidor (TenantUser.customer_id), nunca de
  algo que mande el navegador. Ningun endpoint de aqui recibe un customer_id.
- Toda consulta filtra por tenant Y por ese cliente. Lo de otro cliente responde 404 (no 403): no se
  confirma ni que exista.
- Salidas armadas a mano, campo por campo: nada de costos, horas, montos internos, asignaciones,
  bitacoras ni notas internas. De las notas del ticket solo salen las publicas (internal = False).
- Los endpoints internos los bloquea get_principal (core/deps.py) para cualquier rol de cliente.
"""

from __future__ import annotations

import hashlib
import html
import secrets
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, get_session_principal
from ..models import (
    Customer,
    CustomerAsset,
    Invoice,
    MaintenanceContract,
    Media,
    Quote,
    SupportNote,
    SupportTicket,
    TenantUser,
    User,
)
from ..services import sla as slasvc
from ..services.client_access import invite_client
from ..services.documents import audit
from ..services.mail import notify_roles, queue_email
from ..services.render import render_html, render_pdf
from ..services.sequences import next_number
from .media import MAX_IMAGE, public_url, sniff
from .public_incidents import _CONTROL

router = APIRouter(prefix="/cliente", tags=["portal del cliente"])

OPEN_STATES = ("nuevo", "asignado", "en_proceso", "esperando_cliente")
TIPOS = {"falla": "soporte", "garantia": "garantia", "mantenimiento": "mantenimiento", "otro": "consulta"}
TIPO_LABEL = {"falla": "Falla", "garantia": "Garantía", "mantenimiento": "Mantenimiento", "otro": "Consulta"}
# lo que el cliente ve de cada documento: borradores y anulados no son asunto suyo
QUOTE_VISIBLE = ("enviada", "convertida", "vencida")
INVOICE_VISIBLE = ("enviada", "pagada", "parcial", "vencida")
MAX_PHOTOS = 8


def get_client(p: Principal = Depends(get_session_principal), db: Session = Depends(get_db)) -> Principal:
    """Solo roles de cliente con un cliente ligado, activo y del mismo tenant."""
    if not p.is_client or not p.can("portal", "ver"):
        raise HTTPException(403, "Esta sección es del portal de clientes")
    c = db.get(Customer, p.customer_id) if p.customer_id else None
    if not c or c.tenant_id != p.tenant.id or not c.active:
        raise HTTPException(403, "Su cuenta no está ligada a un cliente activo. Escríbanos para revisarlo.")
    return p


def need(action: str):
    def _dep(p: Principal = Depends(get_client)) -> Principal:
        if not p.can("portal", action):
            raise HTTPException(403, "Su usuario no tiene acceso a esta sección")
        return p

    return _dep


def _aware(dt: datetime | None) -> datetime | None:
    return dt if not dt or dt.tzinfo else dt.replace(tzinfo=UTC)


def _ticket(db: Session, tid: int, p: Principal) -> SupportTicket:
    t = db.get(SupportTicket, tid)
    if not t or t.tenant_id != p.tenant.id or t.customer_id != p.customer_id:
        raise HTTPException(404, "Ticket no encontrado")
    return t


def _puede_comentar(t: SupportTicket, p: Principal) -> bool:
    return t.opened_by == p.user.id or p.can("portal", "comentar_todo")


def _autor(db: Session, user_id: int | None, p: Principal) -> tuple[str, bool]:
    """(nombre visible, es del equipo de Crimson). Del equipo solo sale el nombre de pila."""
    if not user_id:
        return "Equipo Crimson", True
    u = db.get(User, user_id)
    m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == p.tenant.id, TenantUser.user_id == user_id))
    if m and m.customer_id == p.customer_id:
        return (u.full_name if u else "Cliente"), False
    nombre = (u.full_name.split()[0] if u and u.full_name else "Equipo")
    return f"{nombre} · Crimson", True


def _origen(db: Session, t: SupportTicket, p: Principal) -> str:
    """Quien lo abrio, dicho para el cliente: "yo", "empresa" (un companero) o "crimson" (lo abrio el equipo)."""
    if t.opened_by == p.user.id:
        return "yo"
    if t.opened_by:
        m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == p.tenant.id, TenantUser.user_id == t.opened_by))
        if m and m.customer_id == p.customer_id:
            return "empresa"
    return "crimson"


def _ticket_out(db: Session, t: SupportTicket, p: Principal, full: bool = False) -> dict:
    a = db.get(CustomerAsset, t.asset_id) if t.asset_id else None
    out = {
        "id": t.id,
        "number": t.number,
        "subject": t.subject,
        "kind": t.kind,
        "priority": t.priority,
        "status": t.status,
        "abierto": t.status in OPEN_STATES,
        "asset": a.name if a and a.customer_id == p.customer_id else None,
        "mine": t.opened_by == p.user.id,
        "origin": _origen(db, t, p),
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        # la promesa de primera respuesta; nada del origen del SLA ni de contratos
        "respuesta_antes_de": t.due_at if not t.first_reply_at else None,
        "resolved_at": t.resolved_at,
    }
    if full:
        out["description"] = t.description
        out["photos"] = list(t.photos or [])
        out["solution"] = t.solution if t.status in ("resuelto", "cerrado") else None
        # del equipo de Crimson solo el nombre de pila, igual que en la conversacion
        out["opened_by"] = _autor(db, t.opened_by, p)[0] if t.opened_by else ((t.contact or {}).get("name") or "Equipo Crimson")
        out["can_comment"] = _puede_comentar(t, p) and t.status != "cerrado"
        notas = []
        for n in t.notes:
            if n.internal:
                continue  # las notas internas del equipo NUNCA salen al portal
            nombre, equipo = _autor(db, n.user_id, p)
            notas.append({"id": n.id, "body": n.body, "photos": list(n.photos or []), "author": nombre, "team": equipo, "created_at": n.created_at})
        out["messages"] = notas
    return out


def _fotos_propias(db: Session, p: Principal, urls: list[str]) -> list[str]:
    """Solo fotos que ESTE usuario subio por /cliente/media: no se puede colgar en el ticket una URL externa
    ni un archivo de la biblioteca interna de Crimson."""
    if len(urls) > MAX_PHOTOS:
        raise HTTPException(422, f"Máximo {MAX_PHOTOS} fotografías")
    limpias = []
    for u in urls:
        key = str(u).rsplit("/media/f/", 1)[-1] if "/media/f/" in str(u) else ""
        m = db.scalar(select(Media).where(Media.key == key, Media.tenant_id == p.tenant.id, Media.created_by == p.user.id)) if key else None
        if not m:
            raise HTTPException(422, "Una de las fotografías no es válida. Vuelva a subirla.")
        limpias.append(public_url(m))
    return limpias


# ---------- Inicio ----------
@router.get("/inicio")
def inicio(p: Principal = Depends(get_client), db: Session = Depends(get_db)):
    c = db.get(Customer, p.customer_id)
    abiertos = db.scalars(
        select(SupportTicket)
        .where(SupportTicket.tenant_id == p.tenant.id, SupportTicket.customer_id == p.customer_id, SupportTicket.status.in_(OPEN_STATES))
        .order_by(SupportTicket.id.desc())
    ).all()
    visitas = db.scalars(
        select(MaintenanceContract)
        .where(
            MaintenanceContract.tenant_id == p.tenant.id,
            MaintenanceContract.customer_id == p.customer_id,
            MaintenanceContract.active,
            MaintenanceContract.next_date.is_not(None),
        )
        .order_by(MaintenanceContract.next_date)
    ).all()
    equipos = db.scalar(
        select(func.count()).select_from(CustomerAsset).where(CustomerAsset.tenant_id == p.tenant.id, CustomerAsset.customer_id == p.customer_id)
    )
    return {
        "customer": {"name": c.name},
        "company": p.tenant.name,
        "role": p.role,
        "open_tickets": [_ticket_out(db, t, p) for t in abiertos[:10]],
        "open_count": len(abiertos),
        "next_visit": _contrato_out(visitas[0]) if visitas else None,
        "assets_count": equipos or 0,
    }


# ---------- Tickets ----------
@router.get("/tickets")
def tickets(estado: str = "todos", p: Principal = Depends(need("tickets")), db: Session = Depends(get_db)):
    q = select(SupportTicket).where(SupportTicket.tenant_id == p.tenant.id, SupportTicket.customer_id == p.customer_id)
    if estado == "abiertos":
        q = q.where(SupportTicket.status.in_(OPEN_STATES))
    elif estado == "cerrados":
        q = q.where(SupportTicket.status.not_in(OPEN_STATES))
    return [_ticket_out(db, t, p) for t in db.scalars(q.order_by(SupportTicket.id.desc()).limit(200))]


@router.get("/tickets/{tid}")
def ticket(tid: int, p: Principal = Depends(need("tickets")), db: Session = Depends(get_db)):
    return _ticket_out(db, _ticket(db, tid, p), p, full=True)


class TicketIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: str = Field(pattern="^(falla|garantia|mantenimiento|otro)$")
    priority: str = Field("media", pattern="^(baja|media|alta|critica)$")
    subject: str = Field(min_length=3, max_length=150)
    description: str = Field(min_length=10, max_length=3000)
    location: str | None = Field(None, max_length=200)
    asset_id: int | None = None
    photos: list[str] = Field(default_factory=list)

    @field_validator("subject", "description", "location")
    @classmethod
    def _sin_control(cls, v):
        return _CONTROL.sub("", v) if isinstance(v, str) else v


@router.post("/tickets", status_code=201)
def ticket_create(data: TicketIn, p: Principal = Depends(need("tickets")), db: Session = Depends(get_db)):
    if data.asset_id:
        a = db.get(CustomerAsset, data.asset_id)
        if not a or a.tenant_id != p.tenant.id or a.customer_id != p.customer_id:
            raise HTTPException(404, "Equipo no encontrado")
    fotos = _fotos_propias(db, p, data.photos)
    number, _ = next_number(db, p.tenant.id, "TCK")
    t = SupportTicket(
        tenant_id=p.tenant.id,
        number=number,
        customer_id=p.customer_id,  # de la membresia, nunca del cuerpo
        asset_id=data.asset_id,
        contact={"name": p.user.full_name, "email": p.user.email, "location": data.location, "origen": "portal"},
        kind=TIPOS[data.kind],
        channel="portal",
        level=1,
        priority=data.priority,  # la elige el cliente; soporte la ajusta y el SLA se recalcula
        status="nuevo",
        subject=f"{TIPO_LABEL[data.kind]} · {data.subject}"[:200],
        description=data.description + (f"\n\nUbicación: {data.location}" if data.location else ""),
        photos=fotos,
        tags=["portal"],
        opened_by=p.user.id,
    )
    slasvc.aplicar(db, p.tenant, t, datetime.now(UTC))  # misma cascada: contrato > empresa > defecto
    db.add(t)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "create_portal", "support_ticket", t.id, {"prioridad": t.priority}, ip=p.ip)
    c = db.get(Customer, p.customer_id)
    e = html.escape
    notify_roles(
        db,
        p.tenant,
        ("admin", "supervisor"),
        f"Ticket del portal {t.number} · {c.name[:40]}",
        f"<p><b>{e(c.name)}</b> abrió <b>{e(t.number)}</b> desde el portal de clientes.</p>"
        f"<p>{e(t.subject)} · prioridad indicada: {e(t.priority)}</p><blockquote>{e(data.description)}</blockquote>"
        f"<p>Primera respuesta antes de {t.sla['respuesta']} h.</p>",
        "support_ticket",
        t.id,
    )
    db.commit()
    return _ticket_out(db, t, p, full=True)


class CommentIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    body: str = Field(min_length=1, max_length=3000)
    photos: list[str] = Field(default_factory=list)

    @field_validator("body")
    @classmethod
    def _sin_control(cls, v):
        return _CONTROL.sub("", v)


@router.post("/tickets/{tid}/comments", status_code=201)
def comment(tid: int, data: CommentIn, p: Principal = Depends(need("tickets")), db: Session = Depends(get_db)):
    t = _ticket(db, tid, p)
    if not _puede_comentar(t, p):
        raise HTTPException(403, "Solo quien abrió el ticket o el administrador de su empresa puede escribir en él")
    if t.status == "cerrado":
        raise HTTPException(409, "Este ticket ya está cerrado. Abra uno nuevo si el problema sigue.")
    fotos = _fotos_propias(db, p, data.photos)
    # nota publica del cliente: no cuenta como primera respuesta de Crimson (ese reloj es del equipo)
    t.notes.append(SupportNote(tenant_id=t.tenant_id, user_id=p.user.id, body=data.body, internal=False, photos=fotos))
    if t.status in ("esperando_cliente", "resuelto"):
        t.status = "en_proceso"  # el cliente contesto o dice que sigue fallando: vuelve a la cola
        t.resolved_at = None
    audit(db, p.tenant.id, p.user.id, "comment_portal", "support_ticket", t.id, ip=p.ip)
    destino = db.get(User, t.assigned_to) if t.assigned_to else None
    asunto = f"El cliente respondió en {t.number}"
    cuerpo = f"<p><b>{html.escape(p.user.full_name)}</b> escribió en <b>{html.escape(t.number)}</b>:</p><blockquote>{html.escape(data.body)}</blockquote>"
    if destino and destino.email:
        queue_email(db, p.tenant, destino.email, asunto, cuerpo, "support_ticket", t.id)
    else:
        notify_roles(db, p.tenant, ("admin", "supervisor"), asunto, cuerpo, "support_ticket", t.id)
    db.commit()
    return _ticket_out(db, t, p, full=True)


@router.post("/media", status_code=201)
async def upload(file: UploadFile = File(...), p: Principal = Depends(need("tickets")), db: Session = Depends(get_db)):
    """Fotos para un ticket. Solo imagenes (nada de PDF ni SVG), firma de bytes, tope de 5 MB."""
    data = await file.read(MAX_IMAGE + 1)
    kind = sniff(data)
    if not kind or not kind.startswith("image/"):
        raise HTTPException(415, "Formato no permitido: use una foto PNG, JPG, WEBP o GIF")
    if len(data) > MAX_IMAGE:
        raise HTTPException(413, "La foto es muy grande (máximo 5 MB)")
    digest = hashlib.sha256(data).hexdigest()
    same = db.scalar(select(Media).where(Media.tenant_id == p.tenant.id, Media.sha256 == digest, Media.created_by == p.user.id))
    if not same:
        same = Media(tenant_id=p.tenant.id, key=secrets.token_urlsafe(18), filename="foto-portal", content_type=kind, size=len(data), sha256=digest, data=data, created_by=p.user.id)
        db.add(same)
        db.flush()
        audit(db, p.tenant.id, p.user.id, "upload_portal", "media", same.id, {"type": kind, "size": len(data)}, ip=p.ip)
        db.commit()
    # misma forma que /media para reusar PhotoStrip
    return {"id": same.id, "key": same.key, "url": public_url(same), "filename": same.filename, "content_type": same.content_type, "size": same.size}


# ---------- Equipos y mantenimientos ----------
@router.get("/equipos")
def equipos(p: Principal = Depends(get_client), db: Session = Depends(get_db)):
    hoy = date.today()
    rows = db.scalars(
        select(CustomerAsset)
        .where(CustomerAsset.tenant_id == p.tenant.id, CustomerAsset.customer_id == p.customer_id)
        .order_by(CustomerAsset.location, CustomerAsset.name)
    ).all()
    # sin IP, MAC, firmware, proveedor ni factura de compra: datos tecnicos/internos de Crimson
    return [
        {
            "id": a.id,
            "name": a.name,
            "model": a.model,
            "serial": a.serial,
            "location": a.location,
            "installed_at": a.installed_at,
            "warranty_until": a.warranty_until,
            "in_warranty": bool(a.warranty_until and a.warranty_until >= hoy),
            "status": a.status,
            "photos": list(a.photos or []),
        }
        for a in rows
    ]


def _contrato_out(c: MaintenanceContract) -> dict:
    return {"id": c.id, "name": c.name, "kind": c.kind, "every_months": c.every_months, "next_date": c.next_date, "last_done": c.last_done, "scope": c.scope}


@router.get("/mantenimientos")
def mantenimientos(p: Principal = Depends(get_client), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(MaintenanceContract)
        .where(MaintenanceContract.tenant_id == p.tenant.id, MaintenanceContract.customer_id == p.customer_id, MaintenanceContract.active)
        .order_by(MaintenanceContract.next_date)
    ).all()
    return [_contrato_out(c) for c in rows]  # sin montos ni notas internas


# ---------- Documentos (solo cliente_admin) ----------
def _doc_out(d, kind: str) -> dict:
    out = {
        "id": d.id,
        "kind": kind,
        "number": d.number,
        "issue_date": d.issue_date,
        "due_date": d.due_date,
        "currency": d.currency,
        "total": d.total,
        "status": d.status,
    }
    if kind == "invoices":
        out["balance"] = d.balance
    return out


@router.get("/documentos")
def documentos(p: Principal = Depends(need("documentos")), db: Session = Depends(get_db)):
    qs = db.scalars(
        select(Quote)
        .where(Quote.tenant_id == p.tenant.id, Quote.customer_id == p.customer_id, Quote.status.in_(QUOTE_VISIBLE))
        .order_by(Quote.issue_date.desc(), Quote.id.desc())
        .limit(200)
    ).all()
    fs = db.scalars(
        select(Invoice)
        .where(
            Invoice.tenant_id == p.tenant.id,
            Invoice.customer_id == p.customer_id,
            or_(Invoice.status.in_(INVOICE_VISIBLE), (Invoice.einvoice_status == "aceptada") & (Invoice.status != "anulada")),
        )
        .order_by(Invoice.issue_date.desc(), Invoice.id.desc())
        .limit(200)
    ).all()
    return {"quotes": [_doc_out(d, "quotes") for d in qs], "invoices": [_doc_out(d, "invoices") for d in fs]}


def _documento(db: Session, kind: str, did: int, p: Principal):
    if kind == "quotes":
        d = db.get(Quote, did)
        ok = d is not None and d.status in QUOTE_VISIBLE
    elif kind == "invoices":
        d = db.get(Invoice, did)
        ok = d is not None and (d.status in INVOICE_VISIBLE or (d.einvoice_status == "aceptada" and d.status != "anulada"))
    else:
        raise HTTPException(404, "Documento no encontrado")
    if not ok or d.tenant_id != p.tenant.id or d.customer_id != p.customer_id:
        raise HTTPException(404, "Documento no encontrado")
    return d


@router.get("/documentos/{kind}/{did}/pdf")
def documento_pdf(kind: str, did: int, p: Principal = Depends(need("documentos")), db: Session = Depends(get_db)):
    d = _documento(db, kind, did, p)
    html_doc = render_html(db, d, p.tenant)  # la misma plantilla que recibe el cliente por correo (sin notas internas)
    pdf = render_pdf(html_doc)
    audit(db, p.tenant.id, p.user.id, "download_portal", kind[:-1], d.id, ip=p.ip)
    db.commit()
    if pdf is None:
        return HTMLResponse(html_doc, headers={"X-PDF-Fallback": "html"})
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{d.number}.pdf"'})


# ---------- Usuarios de mi empresa (solo cliente_admin) ----------
@router.get("/usuarios")
def usuarios(p: Principal = Depends(need("usuarios")), db: Session = Depends(get_db)):
    from .client_access import portal_users

    data = portal_users(db, p.tenant.id, p.customer_id)
    for u in data["users"]:
        u["me"] = u["id"] == p.user.id
    return data


class InviteIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: EmailStr
    name: str | None = Field(None, max_length=120)


@router.post("/usuarios", status_code=201)
def invitar(data: InviteIn, p: Principal = Depends(need("usuarios")), db: Session = Depends(get_db)):
    """El encargado del cliente invita a su gente, siempre como cliente_usuario y siempre a SU empresa."""
    c = db.get(Customer, p.customer_id)
    try:
        inv = invite_client(db, p.tenant, c, str(data.email), "cliente_usuario", p.user.id, name=data.name)
    except HTTPException as ex:
        if ex.status_code == 409:  # sin revelar si el correo es de alguien de Crimson o de otro cliente
            raise HTTPException(409, "No se pudo invitar a ese correo. Si ya tiene acceso, puede entrar con su contraseña.") from None
        raise
    audit(db, p.tenant.id, p.user.id, "invite_portal", "customer_portal", c.id, ip=p.ip)
    db.commit()
    return inv


class UsuarioIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool


@router.patch("/usuarios/{uid}")
def usuario_update(uid: int, data: UsuarioIn, p: Principal = Depends(need("usuarios")), db: Session = Depends(get_db)):
    m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == p.tenant.id, TenantUser.user_id == uid, TenantUser.customer_id == p.customer_id))
    if not m:
        raise HTTPException(404, "Usuario no encontrado")
    # solo usuarios comunes: a otro administrador (o a si mismo) lo maneja Crimson
    if m.role_code != "cliente_usuario" or uid == p.user.id:
        raise HTTPException(403, "Ese usuario lo administra Crimson. Escríbanos si necesita cambiarlo.")
    m.active = data.active
    audit(db, p.tenant.id, p.user.id, "update_portal", "customer_portal_user", uid, {"active": data.active}, ip=p.ip)
    db.commit()
    return {"ok": True}
