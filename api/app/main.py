"""App factory FastAPI · monolito modular (core · catalogo · ventas · pagos · publico)."""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request

from .core.config import settings
from .routers import (
    accounting,
    allies,
    ally_admin,
    ally_portal,
    approvals,
    auth,
    banking,
    cabys_masivo,
    catalog,
    client_access,
    client_portal,
    commissions,
    core,
    crm_extra,
    data_export,
    events,
    fieldwork,
    hacienda_settings,
    imports,
    media,
    ops,
    payroll,
    pipeline,
    pos,
    projects,
    public,
    public_access,
    public_api,
    public_incidents,
    reception,
    roles_admin,
    sales,
    stock,
    store,
    store_checkout,
    store_showcase,
    supplier_catalog,
    support,
    support_desk,
    users_admin,
    vehicles,
    warranties,
    webhooks,
)
from .routers import bitacora as bitacora_router
from .routers import fiscal_queue as fiscal_queue_router
from .routers import invoice_proofs as invoice_proofs_router
from .routers import settings as settings_router
from .routers import store_inventory as store_inventory_router

log = logging.getLogger("crimson")


def create_app() -> FastAPI:
    from .core import secretbox
    from .core.logging import setup_logging

    setup_logging()  # INFO a stdout, WARNING+ a stderr (Railway marcaba todo como error); uvicorn incluido
    secretbox.keys()  # registra (INFO) solo los kid y sha256(llave)[:8] de FISCAL_KEYS: api y worker deben coincidir
    if settings.is_prod and settings.proxy_shared_key is None:
        # plan 1.10: sin la llave el API ve todo el panel y la tienda con la IP de salida del nginx del portal: los
        # limites por IP (login, checkout, Mi pedido, comprobantes...) pasan a ser globales y la bitacora no muestra la
        # IP real. No se impide el arranque (tumbaria el panel y la tienda); /health lo dice en "proxy_key".
        log.error(
            "PROXY_SHARED_KEY falta (o tiene menos de 32 caracteres): los limites por IP son GLOBALES. Cargar la misma "
            "llave en portal y api y verificar /api/auth/ip-check por el panel (debe decir 'firmadas' con la IP propia)."
        )
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Facturacion, cobros y control financiero · Crimson Consulting x JC Analytics",
        docs_url="/docs" if not settings.is_prod else None,
        redoc_url=None,
    )
    from .core.bodylimit import BodyLimitMiddleware
    from .core.clientip import ClientIPMiddleware
    from .core.cors import SplitCORSMiddleware, log_origins, split_origins

    app.add_middleware(BodyLimitMiddleware)  # antes que CORS: el 413 lleva sus encabezados
    # cookies solo para el panel (en produccion, solo el origen de PUBLIC_BASE_URL); la landing sin cookies y solo en sus
    # rutas publicas (core/cors.py)
    origins = split_origins(list(settings.cors_origins), list(settings.landing_origins), settings.public_base_url, settings.is_prod)
    log_origins(origins)
    app.add_middleware(SplitCORSMiddleware, origins=origins)

    from fastapi.responses import JSONResponse

    from .services.fx import FxMissing

    @app.exception_handler(FxMissing)
    async def fx_missing(request: Request, exc: FxMissing):
        # plan 1.12: sin tipo de cambio nunca se convierte con 1; lo que lo necesita responde un 422 claro
        return JSONResponse(status_code=422, content={"detail": exc.detail})

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

    # el ultimo que se agrega es el de afuera: la IP real queda puesta antes de CORS, limitadores y request_log
    app.add_middleware(ClientIPMiddleware)

    app.include_router(core.router)
    app.include_router(auth.router)
    app.include_router(catalog.router)
    app.include_router(cabys_masivo.router)
    app.include_router(sales.router)
    app.include_router(ops.router)
    app.include_router(bitacora_router.router)  # antes que ajustes: /settings/audit
    app.include_router(hacienda_settings.router)  # antes que ajustes: /settings/hacienda
    app.include_router(fiscal_queue_router.router)  # cola "Por emitir" (plan 1.7)
    app.include_router(settings_router.router)
    app.include_router(users_admin.router)  # Ajustes -> Usuarios (/settings/users, /settings/invitations)
    app.include_router(roles_admin.router)  # Ajustes -> Usuarios -> Roles (/settings/roles)
    app.include_router(webhooks.router)
    app.include_router(store.router)
    app.include_router(store_inventory_router.router)
    app.include_router(store_checkout.router)
    app.include_router(invoice_proofs_router.router)  # comprobantes de la pagina de pago de facturas (/pagar)
    app.include_router(store_showcase.router)
    app.include_router(store_showcase.pub)
    app.include_router(store_showcase.seo)
    app.include_router(reception.router)
    app.include_router(accounting.router)
    app.include_router(public_api.admin)
    app.include_router(public_api.v1)
    app.include_router(media.router)
    app.include_router(crm_extra.router)
    app.include_router(payroll.router)
    app.include_router(banking.router)
    app.include_router(events.router)
    app.include_router(events.pub)
    app.include_router(pos.router)
    app.include_router(imports.router)
    app.include_router(supplier_catalog.router)
    app.include_router(support.router)
    app.include_router(pipeline.router)
    app.include_router(allies.router)
    app.include_router(approvals.router)
    app.include_router(fieldwork.router)
    app.include_router(projects.router)
    app.include_router(stock.router)
    app.include_router(commissions.router)  # comisiones, ganancias de aliados y Mis numeros (economia)
    app.include_router(support_desk.router)
    app.include_router(warranties.router)  # garantias: revision de Crimson -> distribuidor -> cliente
    app.include_router(public.router)
    app.include_router(public_incidents.router)
    app.include_router(public_access.router)
    app.include_router(client_access.router)
    app.include_router(client_portal.router)
    app.include_router(ally_admin.router)  # Administracion -> Aliados (/allies)
    app.include_router(ally_portal.router)  # portal de aliados (/aliado): solo get_ally
    app.include_router(vehicles.router)
    app.include_router(data_export.router)
    return app


app = create_app()
