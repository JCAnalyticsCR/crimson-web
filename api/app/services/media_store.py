"""Donde viven los bytes de multimedia y como se leen: en Postgres (media.data) o en el Storage Bucket de Railway.

Reglas:
- backend(): "s3" solo con MEDIA_STORAGE=s3 Y las variables del bucket completas (BUCKET, ENDPOINT, ACCESS_KEY_ID,
  SECRET_ACCESS_KEY; REGION opcional, "auto"). Si falta algo, "db" y un aviso en el log: nunca tumba el arranque.
- attach(m, data): pone los bytes de una fila NUEVA. Con "s3" sube el objeto media/<empresa>/<key> (llave estable: la
  de la URL publica, que nunca cambia) y la fila queda storage="s3" sin data. Si el bucket falla, la fila queda en la
  base como siempre: una subida nunca se pierde porque el bucket este caido.
- Leer lo decide la FILA, no la variable: storage="s3" se lee del bucket (si el bucket no responde y la fila todavia
  tiene data, de la base); storage="db" de la base.
- response(): db -> los bytes como siempre. s3 -> streaming por bloques de 64 KB (la RAM del api no crece con el
  tamano del archivo). Un PUBLICO con MEDIA_PUBLIC_REDIRECT=1 -> 302 a una URL prefirmada de vida corta. Un PRIVADO
  nunca sale con URL prefirmada: pasa la firma propia (media_access) y se entrega en streaming.
- forget(): borra el objeto (despues del commit; si falla queda un huerfano que no se sirve ni se respalda).

Por que streaming y no 302 por defecto para los publicos: mantiene la URL, la cache de un ano (immutable), el ETag y
las cabeceras de seguridad (CSP, nosniff) exactamente como hoy, y la tienda, el SEO y los correos no notan el cambio. El
egreso del api es el mismo de hoy (hoy tambien sirve todos los bytes). El 302 queda listo para cuando el egreso pese:
el egreso del bucket en Railway es gratis.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
from fastapi import HTTPException
from starlette.background import BackgroundTask
from starlette.responses import RedirectResponse, Response, StreamingResponse

from ..core.config import settings
from .bucket_s3 import S3Client, S3Config, S3Error

log = logging.getLogger("crimson.media")

CHUNK = 64 * 1024
PUBLIC_CACHE = "public, max-age=31536000, immutable"

# pruebas: un httpx.MockTransport que hace de bucket (tests/test_media_bucket.py)
transport: httpx.BaseTransport | None = None

_lock = threading.Lock()
_state: dict = {"cfg": None, "client": None, "transport": None, "warned": False}


def config() -> S3Config | None:
    s = settings
    secret = s.media_s3_secret_access_key.get_secret_value() if s.media_s3_secret_access_key else ""
    endpoint, bucket, access = (s.media_s3_endpoint or "").strip(), (s.media_s3_bucket or "").strip(), (s.media_s3_access_key_id or "").strip()
    if not (endpoint and bucket and access and secret.strip()):
        return None
    return S3Config(
        endpoint=endpoint,
        bucket=bucket,
        access_key=access,
        secret_key=secret.strip(),
        region=(s.media_s3_region or "").strip() or "auto",
        path_style=bool(s.media_s3_path_style),
    )


def configured() -> bool:
    return config() is not None


def backend() -> str:
    """Donde van los archivos NUEVOS."""
    if settings.media_storage != "s3":
        return "db"
    if not configured():
        if not _state["warned"]:
            _state["warned"] = True
            log.warning("MEDIA_STORAGE=s3 sin las variables del bucket (BUCKET, ENDPOINT, ACCESS_KEY_ID, SECRET_ACCESS_KEY): los archivos siguen en la base")
        return "db"
    return "s3"


def client() -> S3Client:
    cfg = config()
    if cfg is None:
        raise S3Error(0, "SinConfiguracion", "faltan las variables del bucket")
    with _lock:
        if _state["client"] is None or _state["cfg"] != cfg or _state["transport"] is not transport:
            _state.update(cfg=cfg, client=S3Client(cfg, transport=transport), transport=transport)
        return _state["client"]


def reset() -> None:
    with _lock:
        c = _state["client"]
        _state.update(cfg=None, client=None, transport=None, warned=False)
    if c is not None:
        c.close()


def object_key(tenant_id: int, key: str) -> str:
    return f"media/{tenant_id}/{key}"


def key_of(m) -> str:
    return object_key(m.tenant_id, m.key)


def in_bucket(m) -> bool:
    return (getattr(m, "storage", None) or "db") == "s3"


# ---------- escritura ----------
def attach(m, data: bytes) -> str:
    """Bytes de una fila NUEVA (tenant_id, key y content_type ya puestos). Devuelve donde quedaron."""
    if backend() == "s3":
        try:
            client().put(key_of(m), data, m.content_type)
            m.storage, m.data = "s3", None
            return "s3"
        except (S3Error, ValueError) as e:
            log.warning("bucket no disponible al subir un archivo (%s): queda en la base", e)
    m.storage, m.data = "db", data
    return "db"


def forget(m) -> None:
    """Borra el objeto del bucket de una fila ya borrada (llamar despues del commit). Nunca lanza."""
    if not in_bucket(m) or not configured():
        return
    try:
        client().delete(key_of(m))
    except (S3Error, ValueError) as e:
        log.warning("no se pudo borrar el objeto %s del bucket (%s): queda huerfano", key_of(m), e)


# ---------- lectura ----------
def fetch(tenant_id: int, key: str) -> bytes:
    """Bytes de un objeto del bucket (respaldo, migracion). Lanza S3Error."""
    return client().get(object_key(tenant_id, key))


def read(m) -> bytes | None:
    """Bytes completos (logos, PDF de recepcion, adjuntos). Para archivos chicos: lo que se SIRVE va por response()."""
    if in_bucket(m) and configured():
        try:
            data = client().get(key_of(m))
            if m.size is None or len(data) == m.size:
                return data
            log.error("el objeto %s del bucket mide %s y la fila dice %s", key_of(m), len(data), m.size)
        except (S3Error, ValueError) as e:
            log.warning("no se pudo leer %s del bucket (%s)", key_of(m), e)
    data = m.data  # columna diferida: solo aqui se carga
    return bytes(data) if data is not None else None


def _stream(r: httpx.Response) -> Iterator[bytes]:
    try:
        yield from r.iter_bytes(CHUNK)
    finally:
        r.close()


def presigned_public_url(m, disposition: str, now: float | None = None) -> tuple[str, int]:
    """(url, segundos que el 302 se puede guardar en cache). La hora de la firma se redondea a la ventana
    MEDIA_PRESIGN_SECONDS: dentro de la misma ventana la URL es identica y el navegador reutiliza la imagen."""
    if m.visibility == "private":
        raise ValueError("un archivo privado nunca sale con URL prefirmada")
    w = int(settings.media_presign_seconds)
    t = int(now if now is not None else time.time())
    start = t - t % w
    url = client().presign_get(
        key_of(m),
        2 * w,  # vale entre w y 2w segundos desde ahora
        now=datetime.fromtimestamp(start, UTC),
        response={"response-content-type": m.content_type, "response-content-disposition": disposition, "response-cache-control": PUBLIC_CACHE},
    )
    return url, max(0, start + 2 * w - t - 300)


def response(m, headers: dict[str, str], *, allow_redirect: bool = False) -> Response:
    """La respuesta HTTP con el archivo. headers: las de siempre (Cache-Control, Content-Disposition, CSP...).
    allow_redirect: solo la ruta publica /media/f/{key}, y solo vale para archivos publicos."""
    if in_bucket(m) and configured():
        if allow_redirect and settings.media_public_redirect and m.visibility != "private":
            try:
                url, max_age = presigned_public_url(m, headers.get("Content-Disposition") or "inline")
                return RedirectResponse(url, status_code=302, headers={"Cache-Control": f"public, max-age={max_age}", "Referrer-Policy": "no-referrer"})
            except (S3Error, ValueError) as e:
                log.warning("no se pudo prefirmar %s (%s): se entrega en streaming", key_of(m), e)
        try:
            r = client().open(key_of(m))
        except (S3Error, ValueError) as e:
            log.warning("no se pudo abrir %s del bucket (%s)", key_of(m), e)
        else:
            h = dict(headers)
            if r.headers.get("content-length"):
                h["Content-Length"] = r.headers["content-length"]
            return StreamingResponse(_stream(r), media_type=m.content_type, headers=h, background=BackgroundTask(r.close))
    data = m.data
    if data is None:
        raise HTTPException(503, "El archivo no está disponible en este momento. Intente de nuevo en unos minutos.", headers={"Retry-After": "60"})
    return Response(bytes(data), media_type=m.content_type, headers=headers)
