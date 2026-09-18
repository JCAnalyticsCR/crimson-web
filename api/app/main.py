"""App factory FastAPI · monolito modular (core · catalogo · ventas · pagos · publico)."""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .core.config import settings
from .routers import auth, catalog, core, ops, public, sales, webhooks
from .routers import settings as settings_router

log = logging.getLogger("crimson")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Facturacion, cobros y control financiero · Crimson Consulting x JC Analytics",
        docs_url="/docs" if not settings.is_prod else None,
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    )

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        ms = (time.perf_counter() - t0) * 1000
        # Nunca loguear Authorization ni cuerpos: solo metodo, ruta, estado y duracion
        log.info("%s %s -> %s (%.0f ms)", request.method, request.url.path, response.status_code, ms)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    app.include_router(core.router)
    app.include_router(auth.router)
    app.include_router(catalog.router)
    app.include_router(sales.router)
    app.include_router(ops.router)
    app.include_router(settings_router.router)
    app.include_router(webhooks.router)
    app.include_router(public.router)
    return app


app = create_app()
