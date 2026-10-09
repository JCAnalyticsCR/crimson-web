"""Reporte de incidencias desde la pagina web (crimsoncr.com), sin cuenta de cliente.

Es un endpoint abierto a internet, asi que todo lo que entra se trata como hostil:
- cuerpo con tope de tamano (se corta leyendo el stream, antes de parsear), sin adjuntos;
- validacion estricta con largos maximos y campos extra prohibidos;
- honeypot ("website"): un bot lo llena, una persona no lo ve; se le responde "ok" sin crear nada;
- limite por IP y limite global en memoria (misma clase que el login: sirve con UNA replica de la API);
- la empresa sale de la configuracion del servidor, nunca del cuerpo;
- la respuesta es identica exista o no el correo como cliente (no se puede usar para averiguar clientes).
El portal de clientes con login queda para despues; esto es el camino rapido.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import get_db
from ..core.ratelimit import incident_global_limiter, incident_limiter
from ..models import Customer, SupportTicket, Tenant
from ..services import sla as slasvc
from ..services.documents import audit
from ..services.mail import notify_roles
from ..services.sequences import next_number
from .auth import client_ip

router = APIRouter(prefix="/public", tags=["publico"])

MAX_BODY = 8 * 1024  # 8 KB sobra para un formulario de texto; sin adjuntos en esta version
# tipo que ve el cliente -> tipo interno del ticket
TIPOS = {"falla": "soporte", "garantia": "garantia", "mantenimiento": "mantenimiento", "otro": "consulta"}
TIPO_LABEL = {"falla": "Falla", "garantia": "Garantía", "mantenimiento": "Mantenimiento", "otro": "Otro"}
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class IncidentIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=120)
    company: str | None = Field(None, max_length=160)  # empresa o particular (texto libre; antes decia "residencial", no hay enum que migrar)
    phone: str | None = Field(None, max_length=25, pattern=r"^[0-9+()\s.-]{7,25}$")
    email: EmailStr | None = Field(None, max_length=200)
    kind: str = Field(pattern="^(falla|garantia|mantenimiento|otro)$")
    severity: str = Field(pattern="^(baja|media|alta|critica)$")
    description: str = Field(min_length=10, max_length=3000)
    location: str | None = Field(None, max_length=200)
    website: str | None = Field(None, max_length=200)  # honeypot: oculto en el formulario

    @field_validator("name", "company", "description", "location")
    @classmethod
    def _sin_control(cls, v):
        return _CONTROL.sub("", v) if isinstance(v, str) else v

    @field_validator("phone", "email", "company", "location", "website", mode="before")
    @classmethod
    def _vacio_es_none(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    @model_validator(mode="after")
    def _algun_contacto(self):
        if not self.phone and not self.email:
            raise ValueError("Indique un teléfono o un correo para poder contactarle")
        return self


def _tenant(db: Session) -> Tenant:
    slug = settings.public_incident_tenant
    if slug:
        t = db.scalar(select(Tenant).where(Tenant.slug == slug, Tenant.active))
    else:
        activos = db.scalars(select(Tenant).where(Tenant.active).limit(2)).all()
        t = activos[0] if len(activos) == 1 else None  # con varias empresas hay que configurarlo explicito
    if not t:
        raise HTTPException(503, "El reporte en línea no está disponible. Escríbanos por WhatsApp.")
    return t


def _digitos(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")[-8:]  # Costa Rica: 8 digitos; ignora +506, guiones y espacios


def _buscar_cliente(db: Session, tid: int, email: str | None, phone: str | None) -> Customer | None:
    if email:
        c = db.scalar(select(Customer).where(Customer.tenant_id == tid, func.lower(Customer.email) == email.lower()).order_by(Customer.id))
        if c:
            return c
    tel = _digitos(phone)
    if len(tel) == 8:
        for c in db.scalars(select(Customer).where(Customer.tenant_id == tid, (Customer.phone.is_not(None)) | (Customer.whatsapp.is_not(None)))):
            if tel in (_digitos(c.phone), _digitos(c.whatsapp)):
                return c
    return None


def _crear(db: Session, data: IncidentIn, ip: str) -> str:
    t = _tenant(db)
    c = _buscar_cliente(db, t.id, data.email, data.phone)  # se liga si existe; nunca se crea un cliente desde aqui
    number, _ = next_number(db, t.id, "TCK")
    resumen = data.description.splitlines()[0][:80]
    tk = SupportTicket(
        tenant_id=t.id,
        number=number,
        customer_id=c.id if c else None,
        contact={
            "name": data.name,
            "company": data.company,
            "phone": data.phone,
            "email": str(data.email).lower() if data.email else None,
            "location": data.location,
            "severidad_percibida": data.severity,
            "tipo_reportado": data.kind,
        },
        kind=TIPOS[data.kind],
        channel="web",
        level=1,
        # la severidad la elige el cliente: entra con esa prioridad y soporte la ajusta al revisar (recalcula el SLA)
        priority=data.severity,
        status="nuevo",
        subject=f"{TIPO_LABEL[data.kind]} · {resumen}"[:200],
        description=data.description + (f"\n\nUbicación: {data.location}" if data.location else ""),
        tags=["web"],
    )
    slasvc.aplicar(db, t, tk, datetime.now(UTC))
    db.add(tk)
    db.flush()
    audit(db, t.id, None, "create_web", "support_ticket", tk.id, {"ligado_a_cliente": bool(c)}, ip=ip)
    e = html.escape
    cuerpo = (
        f"<p>Entró un reporte desde la página web: <b>{e(tk.number)}</b></p>"
        f"<p><b>{e(data.name)}</b>{' · ' + e(data.company) if data.company else ''}<br>"
        f"{e(data.phone or '')} {e(str(data.email) if data.email else '')}</p>"
        f"<p>Tipo: {e(TIPO_LABEL[data.kind])} · severidad indicada: {e(data.severity)}"
        f"{' · cliente registrado: ' + e(c.name) if c else ' · sin ficha de cliente'}</p>"
        f"<blockquote>{e(data.description)}</blockquote>"
        f"{'<p>Ubicación: ' + e(data.location) + '</p>' if data.location else ''}"
        f"<p>Primera respuesta antes de {tk.sla['respuesta']} h.</p>"
    )
    notify_roles(db, t, ("admin", "supervisor"), f"Ticket web {tk.number} · {resumen[:50]}", cuerpo, "support_ticket", tk.id)
    db.commit()
    return tk.number


@router.post("/incidents", status_code=201)
async def report_incident(request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    incident_limiter.hit(f"incident:{ip}")
    incident_global_limiter.hit("incident:*")
    largo = request.headers.get("content-length")
    if largo and (not largo.isdigit() or int(largo) > MAX_BODY):
        raise HTTPException(413, "El reporte es demasiado largo")
    raw = b""
    async for chunk in request.stream():  # tope real aunque no venga Content-Length (chunked)
        raw += chunk
        if len(raw) > MAX_BODY:
            raise HTTPException(413, "El reporte es demasiado largo")
    try:
        data = IncidentIn.model_validate_json(raw or b"{}")
    except ValidationError as ex:
        # solo campo y mensaje: nada de eco del valor recibido
        errores = [{"campo": ".".join(str(x) for x in err["loc"]) or "general", "mensaje": err["msg"]} for err in ex.errors(include_input=False)]
        raise HTTPException(422, {"message": "Revise los datos del formulario", "errors": errores}) from None
    if data.website:
        return {"ok": True, "number": None}  # honeypot: al bot se le dice que si, y no se crea nada
    number = await run_in_threadpool(_crear, db, data, ip)
    return {"ok": True, "number": number}


@router.get("/sla")
def public_sla(response: Response, db: Session = Depends(get_db)):
    """Los tiempos de primera respuesta que la pagina le promete al publico. Salen de Ajustes, no de numeros
    escritos a mano en el HTML: si Andres cambia el SLA, la promesa publica cambia con el. Solo horas de
    respuesta por prioridad; nada de contratos ni de clientes."""
    t = _tenant(db)
    response.headers["Cache-Control"] = "public, max-age=300"
    return {pr: v["respuesta"] for pr, v in slasvc.sla_tenant(t).items()}
