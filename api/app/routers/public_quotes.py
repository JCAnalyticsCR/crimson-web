"""Pagina publica de una cotizacion (ventas etapa 3): verla y aceptarla con el enlace que le llego al cliente, sin
iniciar sesion. Todo con limite por IP; nunca costo, margen, notas internas ni otras cotizaciones (services/quote_links)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.clientip import ip_key
from ..core.db import get_db
from ..core.ratelimit import quote_accept_limiter as accept_limiter
from ..core.ratelimit import quote_view_limiter as view_limiter
from ..services import quote_links
from .auth import client_ip

router = APIRouter(prefix="/public/quote", tags=["publico"])

def _ip(request: Request) -> str | None:
    return client_ip(request)


@router.get("/{token}")
def quote_page(token: str, request: Request, db: Session = Depends(get_db)):
    ip = _ip(request)
    view_limiter.hit(f"qview:{ip_key(ip)}")
    t, q, link = quote_links.resolve(db, token)
    quote_links.register_open(db, t, q, link, ip)
    out = quote_links.public_out(db, t, q)
    db.commit()
    return out


@router.get("/{token}/pdf")
def quote_pdf(token: str, request: Request, db: Session = Depends(get_db)):
    view_limiter.hit(f"qview:{ip_key(_ip(request))}")
    t, q, _link = quote_links.resolve(db, token)
    from ..services.render import render_pdf

    html = quote_links.public_html(db, t, q)
    pdf = render_pdf(html)
    if pdf is None:
        return HTMLResponse(html, headers={"X-PDF-Fallback": "html"})
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{q.number}.pdf"'})


class AcceptIn(BaseModel):
    name: str = Field(min_length=3, max_length=160)
    id_number: str = Field(min_length=9, max_length=30)


@router.post("/{token}/accept")
def quote_accept(token: str, data: AcceptIn, request: Request, db: Session = Depends(get_db)):
    ip = _ip(request)
    accept_limiter.hit(f"qaccept:{ip_key(ip)}")
    t, q, _link = quote_links.resolve(db, token)
    quote_links.accept(db, t, q, data.name, data.id_number, ip)
    db.commit()
    return quote_links.public_out(db, t, q)
