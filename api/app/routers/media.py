"""Multimedia: subida de imagenes (productos, logo, eventos) y PDF (adjuntos de gastos).

Reglas: tipo validado por firma de bytes (no por la extension ni el Content-Type del navegador), SVG prohibido
(puede llevar scripts), tope de tamano, URL publica con llave aleatoria y cabeceras que impiden ejecutar contenido.
"""

from __future__ import annotations

import hashlib
import secrets

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import get_db
from ..core.deps import Principal, get_principal
from ..models import Media
from ..services.documents import audit

router = APIRouter(tags=["multimedia"])

MAX_IMAGE = 5 * 1024 * 1024
MAX_PDF = 10 * 1024 * 1024


def sniff(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def public_url(m: Media) -> str:
    return f"{settings.public_base_url.rstrip('/')}/api/media/f/{m.key}"


def _out(m: Media) -> dict:
    return {"id": m.id, "key": m.key, "url": public_url(m), "filename": m.filename, "content_type": m.content_type, "size": m.size, "created_at": m.created_at}


# El tecnico entra aqui con field.crear: su evidencia de instalacion es una foto desde el celular.
UPLOADERS = (
    ("catalog", "crear"),
    ("catalog", "editar"),
    ("accounting", "crear"),
    ("settings", "configurar"),
    ("field", "crear"),
    ("field", "editar"),
    ("assets", "crear"),
)


def _can_upload(p: Principal) -> None:
    if not any(p.can(mod, act) for mod, act in UPLOADERS):
        raise HTTPException(403, "Sin permiso para subir archivos")


@router.post("/media", status_code=201)
async def upload(file: UploadFile = File(...), p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    _can_upload(p)
    data = await file.read(MAX_PDF + 1)
    kind = sniff(data)
    if not kind:
        raise HTTPException(415, "Formato no permitido: use PNG, JPG, WEBP, GIF o PDF")
    limit = MAX_PDF if kind == "application/pdf" else MAX_IMAGE
    if len(data) > limit:
        raise HTTPException(413, f"Archivo muy grande (maximo {limit // (1024 * 1024)} MB)")
    digest = hashlib.sha256(data).hexdigest()
    same = db.scalar(select(Media).where(Media.tenant_id == p.tenant.id, Media.sha256 == digest))
    if same:  # la misma imagen subida dos veces reutiliza el registro
        return _out(same)
    name = "".join(ch if (ch.isascii() and ch.isalnum()) or ch in "._- " else "_" for ch in (file.filename or "archivo"))[:120] or "archivo"
    m = Media(
        tenant_id=p.tenant.id, key=secrets.token_urlsafe(18), filename=name, content_type=kind, size=len(data), sha256=digest, data=data, created_by=p.user.id
    )
    db.add(m)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "upload", "media", m.id, {"type": kind, "size": len(data)}, ip=p.ip)
    db.commit()
    return _out(m)


@router.get("/media")
def list_media(kind: str = "image", p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    q = select(Media).where(Media.tenant_id == p.tenant.id)
    q = q.where(Media.content_type.like("image/%")) if kind == "image" else q.where(Media.content_type == "application/pdf")
    return [_out(m) for m in db.scalars(q.order_by(Media.id.desc()).limit(60))]


@router.delete("/media/{mid}", status_code=204)
def delete_media(mid: int, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    _can_upload(p)
    m = db.get(Media, mid)
    if not m or m.tenant_id != p.tenant.id:
        raise HTTPException(404, "Archivo no encontrado")
    db.delete(m)
    audit(db, p.tenant.id, p.user.id, "delete", "media", mid, ip=p.ip)
    db.commit()


@router.get("/media/f/{key}", tags=["publico"])
def serve(key: str, db: Session = Depends(get_db)):
    m = db.scalar(select(Media).where(Media.key == key))
    if not m:
        raise HTTPException(404, "Archivo no encontrado")
    disp = "inline" if m.content_type.startswith("image/") else "attachment"
    return Response(
        m.data,
        media_type=m.content_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Content-Disposition": f'{disp}; filename="{m.filename}"',
            "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
        },
    )
