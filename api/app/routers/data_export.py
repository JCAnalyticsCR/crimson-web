"""Ajustes -> Respaldo: "Exportar todos mis datos" (salida de datos, documento de Andres seccion 18).

Solo el administrador. Descarga el .zip portable de SU empresa (mismo formato que app.tools.backup), sin
contrasenas, 2FA ni secretos cifrados. Cada descarga queda en la bitacora con el hash del archivo.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..core.db import Base, get_db
from ..core.deps import Principal, require
from ..services import backup as bk
from ..services.documents import audit

router = APIRouter(prefix="/settings", tags=["ajustes"])


def _admin(p: Principal = Depends(require("settings", "configurar"))) -> Principal:
    if p.role != "admin":
        raise HTTPException(403, "Solo el administrador puede exportar todos los datos de la empresa")
    return p


@router.get("/data-export/info")
def export_info(p: Principal = Depends(_admin)):
    return {
        "scope": "tenant",
        "redacted": True,
        "includes": "Todas las tablas de la empresa (clientes, documentos, pagos, inventario, campo, vehiculos, bitacora...) + archivos de multimedia",
        "excludes": ["contrasenas y 2FA", "secretos de pasarelas y API", "sesiones abiertas"],
    }


@router.get("/data-export")
def export_all(background: BackgroundTasks, p: Principal = Depends(_admin), db: Session = Depends(get_db)):
    fd, path = tempfile.mkstemp(prefix="crimson-export-", suffix=".zip")
    os.close(fd)
    try:
        with open(path, "wb") as fh:
            m = bk.write_backup(db.connection(), Base.metadata, fh, tenant=p.tenant, redact=True, include_blobs=True)
    except Exception:
        os.unlink(path)
        raise
    tot = m["totals"]
    audit(
        db,
        p.tenant.id,
        p.user.id,
        "export_all",
        "tenant",
        p.tenant.id,
        {"tables": tot["tables"], "rows": tot["rows"], "files": tot["blobs"], "sha256": m["sha256"]},
        ip=p.ip,
    )
    db.commit()
    background.add_task(os.unlink, path)
    name = f"crimson-datos-{p.tenant.slug}-{datetime.now(UTC).strftime('%Y%m%d-%H%M')}.zip"
    return FileResponse(path, media_type="application/zip", filename=name, headers={"X-Backup-Sha256": m["sha256"], "Cache-Control": "no-store"})
