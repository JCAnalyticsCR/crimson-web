"""Tareas del worker para el catálogo de Eurocomp.

catalog.auto_catalog: crea productos para las familias configuradas en auto_catalog (tenant.settings)
sin llamar a Eurocomp. Se encola desde el endpoint POST /supplier-catalog/suppliers/{sid}/auto-catalog/run
o se llama directamente en modo CLI (ampliar_catalogo.py). Opera en lotes de 50 para no acumular todo
en memoria; hace commit por lote.
"""

from __future__ import annotations

import logging

from celery import shared_task

log = logging.getLogger("crimson.catalogo")


@shared_task(name="catalog.auto_catalog", bind=True, max_retries=1, default_retry_delay=60)
def auto_catalog_task(self, tenant_id: int, user_id: int | None = None):
    """Crea productos del catálogo de Eurocomp para las familias activas en auto_catalog.
    Idempotente: no duplica ni vuelve a crear lo que el admin borró u ocultó."""
    from ..core.db import SessionLocal
    from ..models import Tenant
    from ..services import supplier_sync as ss

    log.info("catalog.auto_catalog: empresa %s, solicitado por usuario %s", tenant_id, user_id)
    with SessionLocal() as db:
        t = db.get(Tenant, tenant_id)
        if t is None:
            log.warning("catalog.auto_catalog: empresa %s no encontrada", tenant_id)
            return {"error": "empresa no encontrada"}
        sup = ss.find_supplier(db, tenant_id)
        result = ss.auto_catalog_run(db, t, sup, commit_batches=True)
        ss.trim_memory()
        log.info("catalog.auto_catalog (empresa %s): %s", tenant_id, result)
        return result
