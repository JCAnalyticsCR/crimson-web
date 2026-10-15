"""Tope del cuerpo de una peticion, antes de que Starlette lo parsee.

Los topes de cada ruta (10 MB por archivo, 20 archivos por lote, 50 MB la importacion) se revisan cuando el multipart
ya se recibio entero y se guardo en archivos temporales (en disco desde 1 MB, hasta 1000 partes, sin tope total): un
solo request podia llenar el disco efimero del contenedor. Aqui se corta antes: por Content-Length si viene, y contando
lo recibido si llega por partes (chunked).
"""

from __future__ import annotations

from fastapi import HTTPException

# La importacion mas grande que se acepta es de 50 MB (zip de XML de Fygaro) + el sobre del multipart.
MAX_BODY = 64 * 1024 * 1024
MESSAGE = "La petición es demasiado grande (máximo 64 MB): divida los archivos en varias cargas"


class BodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        limit = MAX_BODY
        raw = dict(scope.get("headers") or []).get(b"content-length")
        if raw is not None:
            try:
                declared = int(raw)
            except ValueError:
                declared = 0
            if declared > limit:
                await _reject(send)
                return
        total = 0

        async def limited():
            nonlocal total
            message = await receive()
            if message.get("type") == "http.request":
                total += len(message.get("body") or b"")
                if total > limit:
                    # FastAPI re-lanza las HTTPException del cuerpo tal cual (no las vuelve 400)
                    raise HTTPException(413, MESSAGE)
            return message

        await self.app(scope, limited, send)


async def _reject(send) -> None:
    import json

    body = json.dumps({"detail": MESSAGE}).encode()
    await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
