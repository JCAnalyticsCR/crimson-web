"""Ajustes -> Hacienda -> «Prueba real con Hacienda» (ambiente de PRUEBAS, sin cambiar el modo de la empresa).

Lanzar, volver a consultar y descargar los XML pide settings.configurar; lanzar pide ademas la contrasena del
administrador y la casilla de confirmacion. Contabilidad (accounting.ver) ve la lista y el detalle. Nada de esto devuelve
credenciales, token ni PIN; el XML solo sale por las descargas. Ver services/hacienda_prueba.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import secretbox
from ..core.db import get_db
from ..core.deps import Principal, require, require_one_of
from ..core.reauth import require_reauth
from ..models import HaciendaTestRun
from ..services import hacienda_prueba as hp

router = APIRouter(tags=["hacienda"])
BASE = "/settings/hacienda/prueba-real"
REAUTH_MSG = "Confirme con su contraseña de administrador"
XML_KIND = {"fe": "fe_xml", "fe-respuesta": "fe_respuesta_xml", "mr": "mr_xml", "mr-respuesta": "mr_respuesta_xml"}


class PruebaIn(BaseModel):
    admin_password: str | None = Field(None, max_length=200)
    cabys: str = Field("", max_length=20)
    con_mensaje_receptor: bool = True
    confirmo: bool = False


def _run(db: Session, p: Principal, run_id: int) -> HaciendaTestRun:
    run = db.get(HaciendaTestRun, run_id)
    if run is None or run.tenant_id != p.tenant.id:
        raise HTTPException(404, "Prueba no encontrada")
    return run


@router.post(BASE)
def iniciar(data: PruebaIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    require_reauth(p, data.admin_password, REAUTH_MSG)
    if not data.confirmo:
        raise HTTPException(422, "Marque «Entiendo que se enviará un comprobante de prueba a Hacienda» para continuar")
    if not secretbox.available():
        raise HTTPException(503, "Falta configurar FISCAL_KEYS en el servidor")
    try:
        run = hp.iniciar(db, p.tenant, p.user, data.cabys, data.con_mensaje_receptor, ip=p.ip)
    except hp.RequisitosFallan as e:
        return JSONResponse(status_code=422, content={"detail": str(e)[:600], "steps": e.steps})
    return hp.out(db, run)


@router.get(BASE)
def listar(p: Principal = Depends(require_one_of(("settings", "ver"), ("accounting", "ver"))), db: Session = Depends(get_db)):
    if hp.cerrar_vencidas(db, p.tenant):
        db.commit()
    runs = db.scalars(select(HaciendaTestRun).where(HaciendaTestRun.tenant_id == p.tenant.id).order_by(HaciendaTestRun.created_at.desc(), HaciendaTestRun.id.desc()).limit(10)).all()
    can_configure = p.can("settings", "configurar")
    out = {
        "runs": [hp.out(db, r) for r in runs],
        "can_configure": can_configure,
        "can_run": False,
        "blocked_reason": None,
        "cabys_sugerido": hp.cabys_sugerido(db, p.tenant),
    }
    why = hp.blocked_reason(db, p.tenant) if can_configure else "Solo un administrador puede lanzar la prueba real"
    out["can_run"], out["blocked_reason"] = why is None, why
    db.commit()  # perfil y grupos fiscales que falten se crean al leer
    return out


@router.get(BASE + "/{run_id}")
def detalle(run_id: int, p: Principal = Depends(require_one_of(("settings", "ver"), ("accounting", "ver"))), db: Session = Depends(get_db)):
    return hp.out(db, _run(db, p, run_id))


@router.post(BASE + "/{run_id}/consultar")
def consultar(run_id: int, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    _run(db, p, run_id)
    return hp.out(db, hp.consultar(db, p.tenant, run_id, p.user.id))


@router.get(BASE + "/{run_id}/xml/{which}")
def descargar(run_id: int, which: str, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    if which not in XML_KIND:
        raise HTTPException(404, "Documento desconocido (fe, fe-respuesta, mr o mr-respuesta)")
    run = _run(db, p, run_id)
    body = getattr(run, XML_KIND[which])
    if not body:
        raise HTTPException(404, "Ese documento no existe en esta prueba")
    name = f"{run.clave or run.id}-prueba-{which}.xml"
    return Response(body.encode("utf-8"), media_type="application/xml", headers={"Content-Disposition": f'attachment; filename="{name}"'})
