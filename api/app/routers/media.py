"""Multimedia: subida de imagenes (productos, logo, eventos) y PDF (adjuntos de gastos).

Reglas: tipo validado por firma de bytes (no por la extension ni el Content-Type del navegador), SVG prohibido
(puede llevar scripts), tope de tamano, URL con llave aleatoria y cabeceras que impiden ejecutar contenido.
Publico vs privado: ver services/media_access.py. Lo que sube el equipo desde levantamientos, OT, proyectos,
contabilidad o tickets nace privado y solo se entrega con enlace firmado que vence.
"""

from __future__ import annotations

import hashlib
import secrets
import time

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import get_db
from ..core.deps import Principal, get_principal
from ..models import Media
from ..services import media_access as ma
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
    """URL estable que se guarda en los registros (sin firma). Para un privado no alcanza para verlo."""
    return f"{settings.public_base_url.rstrip('/')}/api/media/f/{m.key}"


def _out(m: Media) -> dict:
    out = {"id": m.id, "key": m.key, "url": public_url(m), "filename": m.filename, "content_type": m.content_type, "size": m.size, "created_at": m.created_at}
    # campos nuevos (el contrato viejo sigue igual): url es la estable que se guarda; signed_url la que se muestra
    out["visibility"] = m.visibility or "public"
    out["module"] = m.module
    if out["visibility"] == "private":
        out["signed_url"] = ma.with_signature(out["url"], m.key)
    return out


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


def _can_upload(p: Principal, module: str | None = None) -> None:
    if module:  # evidencia privada: hay que poder trabajar en el modulo al que va el archivo
        if module not in ma.PRIVATE_MODULES:
            raise HTTPException(422, "Módulo de origen inválido")
        if not (p.can(module, "crear") or p.can(module, "editar")):
            raise HTTPException(403, "Sin permiso para subir archivos a este módulo")
        return
    if not any(p.can(mod, act) for mod, act in UPLOADERS):
        raise HTTPException(403, "Sin permiso para subir archivos")


def save_media(
    db: Session,
    tenant_id: int,
    data: bytes,
    filename: str | None,
    user_id: int | None,
    ip: str | None = None,
    visibility: str = "public",
    module: str | None = None,
) -> tuple[Media, bool]:
    """Guarda bytes con las mismas reglas que la subida (firma, tope, dedupe por sha256). Devuelve (media, nuevo).
    Lo usan la subida del portal y los importadores (p. ej. fotos del catalogo de Fygaro). No hace commit.
    El dedupe respeta la visibilidad: una foto privada nunca se reutiliza como imagen de la tienda (ni al reves)."""
    kind = sniff(data)
    if not kind:
        raise HTTPException(415, "Formato no permitido: use PNG, JPG, WEBP, GIF o PDF")
    limit = MAX_PDF if kind == "application/pdf" else MAX_IMAGE
    if len(data) > limit:
        raise HTTPException(413, f"Archivo muy grande (maximo {limit // (1024 * 1024)} MB)")
    digest = hashlib.sha256(data).hexdigest()
    q = select(Media).where(Media.tenant_id == tenant_id, Media.sha256 == digest, Media.visibility == visibility)
    if visibility == "private":
        q = q.where(Media.module == module)
    same = db.scalar(q.order_by(Media.id).limit(1))
    if same:  # la misma imagen subida dos veces reutiliza el registro
        return same, False
    name = "".join(ch if (ch.isascii() and ch.isalnum()) or ch in "._- " else "_" for ch in (filename or "archivo"))[:120] or "archivo"
    m = Media(
        tenant_id=tenant_id,
        key=secrets.token_urlsafe(18),
        filename=name,
        content_type=kind,
        size=len(data),
        sha256=digest,
        data=data,
        created_by=user_id,
        visibility=visibility,
        module=module if visibility == "private" else None,
    )
    db.add(m)
    db.flush()
    info = {"type": kind, "size": len(data)}
    if visibility == "private":
        info.update(visibility="private", module=module)
    audit(db, tenant_id, user_id, "upload", "media", m.id, info, ip=ip)
    return m, True


@router.post("/media", status_code=201)
async def upload(
    file: UploadFile = File(...),
    visibility: str = Query("public", pattern="^(public|private)$"),
    module: str | None = Query(None, max_length=40),
    p: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    """Sin parametros: publico, como siempre (tienda, logo, eventos). Con visibility=private&module=<origen>:
    evidencia interna que solo se ve con enlace firmado."""
    if visibility == "private" and not module:
        raise HTTPException(422, "Un archivo privado necesita el módulo de origen")
    if visibility == "public":
        module = None
    _can_upload(p, module)
    data = await file.read(MAX_PDF + 1)
    m, new = save_media(db, p.tenant.id, data, file.filename, p.user.id, ip=p.ip, visibility=visibility, module=module)
    if new:
        db.commit()
    return _out(m)


def _visible(p: Principal):
    """Filtro SQL de lo que este usuario puede ver: lo publico, lo que el subio y lo privado de modulos que ve."""
    if "*" in p.permissions:
        return Media.tenant_id == p.tenant.id
    mods = [m for m in ma.PRIVATE_MODULES if p.can(m, "ver")]
    conds = [Media.visibility == "public", Media.created_by == p.user.id]
    if mods:
        conds.append(Media.module.in_(mods))
    return or_(*conds)


@router.get("/media")
def list_media(
    kind: str = "image",
    visibility: str | None = Query(None, pattern="^(public|private)$"),
    p: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    q = select(Media).where(Media.tenant_id == p.tenant.id, _visible(p))
    # los comprobantes de pago de la tienda se ven solo desde su pedido, nunca en la galeria
    q = q.where(or_(Media.module.is_(None), Media.module != "sales"))
    q = q.where(Media.content_type.like("image/%")) if kind == "image" else q.where(Media.content_type == "application/pdf")
    if visibility:
        q = q.where(Media.visibility == visibility)
    return [_out(m) for m in db.scalars(q.order_by(Media.id.desc()).limit(60))]


class SignIn(BaseModel):
    urls: list[str] = Field(default_factory=list, max_length=200)


@router.post("/media/sign")
def sign_media(data: SignIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """El portal manda las URL que va a mostrar y recibe con que URL pedir cada una. Publica: la misma.
    Privada que el usuario puede ver: firmada, vence en minutos. Lo que no puede ver (o no es de su empresa)
    no vuelve, y esa foto no se muestra."""
    keys = {k for k in (ma.key_of(u) for u in data.urls) if k}
    rows = {m.key: m for m in db.scalars(select(Media).where(Media.tenant_id == p.tenant.id, Media.key.in_(keys)))} if keys else {}
    out: dict[str, str] = {}
    exp_min: int | None = None
    for u in data.urls:
        k = ma.key_of(u)
        m = rows.get(k) if k else None
        if not m:
            continue
        if m.visibility != "private":
            out[u] = ma.base_url(u)
        elif ma.can_view(p, m):
            exp, s = ma.sign(m.key)
            out[u] = f"{ma.base_url(u)}?e={exp}&s={s}"
            exp_min = exp if exp_min is None else min(exp_min, exp)
    return {"urls": out, "expires_at": exp_min}


@router.delete("/media/{mid}", status_code=204)
def delete_media(mid: int, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    m = db.get(Media, mid)
    if not m or m.tenant_id != p.tenant.id or not ma.can_view(p, m):
        raise HTTPException(404, "Archivo no encontrado")
    _can_upload(p, m.module if m.visibility == "private" else None)
    db.delete(m)
    audit(db, p.tenant.id, p.user.id, "delete", "media", mid, ip=p.ip)
    db.commit()


@router.get("/media/f/{key}", tags=["publico"])
def serve(key: str, e: int | None = None, s: str | None = None, db: Session = Depends(get_db)):
    m = db.scalar(select(Media).where(Media.key == key))
    if not m:
        raise HTTPException(404, "Archivo no encontrado")
    cache = "public, max-age=31536000, immutable"
    if m.visibility == "private":
        st = ma.verify(m.key, e, s)
        if st == "missing":
            raise HTTPException(401, "Archivo privado: se necesita un enlace firmado")
        if st == "expired":
            raise HTTPException(403, "El enlace del archivo venció; volvé a abrirlo desde el sistema")
        if st != "ok":
            raise HTTPException(403, "Enlace inválido")
        # nunca en caches compartidos; el navegador lo guarda a lo sumo hasta que vence la firma
        cache = f"private, max-age={max(0, min(int(e or 0) - int(time.time()), 3600))}"
    disp = "inline" if m.content_type.startswith("image/") else "attachment"
    return Response(
        m.data,
        media_type=m.content_type,
        headers={
            "Cache-Control": cache,
            "Content-Disposition": f'{disp}; filename="{m.filename}"',
            "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )
