"""Solicitud de acceso al portal del cliente desde la pagina publica (sin cuenta).

Regla de seguridad numero uno: registrarse NO da acceso a nada. Esto solo crea una SOLICITUD pendiente;
un humano de Crimson la revisa, la liga a un cliente que el confirma, elige el rol y aprueba. Recien ahi
sale una invitacion con enlace de un solo uso. Asi nadie ve los tickets ni las facturas de una empresa por
escribir su cedula o el correo de otra persona.

Mismas protecciones que /public/incidents (endpoint abierto a internet = todo es hostil):
- tope de tamano leyendo el stream, antes de parsear; sin adjuntos;
- validacion estricta, largos maximos, campos extra prohibidos;
- honeypot ("website"): al bot se le responde igual y no se crea nada;
- limite por IP y global en memoria;
- la empresa sale de la configuracion del servidor, nunca del cuerpo;
- respuesta IDENTICA exista o no el cliente, el correo o una solicitud previa (no sirve para averiguar nada).
"""

from __future__ import annotations

import html

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.ratelimit import access_request_global_limiter, access_request_limiter
from ..models import AccessRequest
from ..services.documents import audit
from ..services.mail import notify_roles
from .auth import client_ip
from .public_incidents import _CONTROL, _tenant

router = APIRouter(prefix="/public", tags=["publico"])

MAX_BODY = 6 * 1024
RESPUESTA = {"ok": True, "message": "Recibimos su solicitud. El equipo de Crimson la revisa y le escribe para darle acceso."}


class AccessRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=120)
    email: EmailStr = Field(max_length=200)  # obligatorio: la invitacion sale a este correo
    phone: str | None = Field(None, max_length=25, pattern=r"^[0-9+()\s.-]{7,25}$")
    company: str = Field(min_length=2, max_length=160)  # empresa o residencial
    id_number: str | None = Field(None, max_length=30, pattern=r"^[0-9A-Za-z\s.-]{5,30}$")  # cedula opcional
    message: str | None = Field(None, max_length=1000)
    website: str | None = Field(None, max_length=200)  # honeypot

    @field_validator("name", "company", "message")
    @classmethod
    def _sin_control(cls, v):
        return _CONTROL.sub("", v) if isinstance(v, str) else v

    @field_validator("phone", "id_number", "message", "website", mode="before")
    @classmethod
    def _vacio_es_none(cls, v):
        return None if isinstance(v, str) and not v.strip() else v


def _crear(db: Session, data: AccessRequestIn, ip: str) -> None:
    t = _tenant(db)
    email = str(data.email).lower()
    # una solicitud pendiente por correo: reenviar el formulario no llena la bandeja (y responde lo mismo)
    ya = db.scalar(
        select(AccessRequest).where(AccessRequest.tenant_id == t.id, func.lower(AccessRequest.email) == email, AccessRequest.status == "pendiente")
    )
    if ya:
        return
    r = AccessRequest(
        tenant_id=t.id,
        name=data.name,
        email=email,
        phone=data.phone,
        company=data.company,
        id_number=data.id_number,
        message=data.message,
        status="pendiente",
        ip=ip[:64],
    )
    db.add(r)
    db.flush()
    audit(db, t.id, None, "create_web", "access_request", r.id, ip=ip)
    e = html.escape
    cuerpo = (
        "<p>Alguien pidió acceso al portal de clientes. <b>No tiene acceso todavía</b>: revise quién es, "
        "líguelo al cliente correcto y apruebe o rechace en <b>Accesos de clientes</b>.</p>"
        f"<p><b>{e(data.name)}</b> · {e(data.company)}<br>{e(email)} {e(data.phone or '')}"
        f"{'<br>Cédula indicada: ' + e(data.id_number) if data.id_number else ''}</p>"
        f"{'<blockquote>' + e(data.message) + '</blockquote>' if data.message else ''}"
    )
    notify_roles(db, t, ("admin", "supervisor"), f"Solicitud de acceso al portal · {data.company[:50]}", cuerpo, "access_request", r.id)
    db.commit()


@router.post("/access-requests", status_code=202)
async def request_access(request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    access_request_limiter.hit(f"access:{ip}")
    access_request_global_limiter.hit("access:*")
    largo = request.headers.get("content-length")
    if largo and (not largo.isdigit() or int(largo) > MAX_BODY):
        raise HTTPException(413, "La solicitud es demasiado larga")
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > MAX_BODY:
            raise HTTPException(413, "La solicitud es demasiado larga")
    try:
        data = AccessRequestIn.model_validate_json(raw or b"{}")
    except ValidationError as ex:
        errores = [{"campo": ".".join(str(x) for x in err["loc"]) or "general", "mensaje": err["msg"]} for err in ex.errors(include_input=False)]
        raise HTTPException(422, {"message": "Revise los datos del formulario", "errors": errores}) from None
    if data.website:
        return RESPUESTA  # honeypot: misma respuesta, nada creado
    await run_in_threadpool(_crear, db, data, ip)
    return RESPUESTA
