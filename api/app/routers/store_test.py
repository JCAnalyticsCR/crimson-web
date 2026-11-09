"""Mi Tienda -> Tienda de pruebas (/store/pruebas): compras de prueba con ONVO en modo Test, solo dentro del panel.

Nunca es publica: no hay ruta /public para esto, cada llamada pide sesion de ADMINISTRADOR real (rol admin con
settings.configurar; sin eso responde 404, como si no existiera), y el regreso de ONVO vuelve al panel. Un usuario de
cliente o de una empresa aliada se queda en el 403 generico de get_principal. Ver services/test_shop.py.
"""

from __future__ import annotations

import json
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import secretbox
from ..core.db import get_db
from ..core.deps import Principal, get_principal
from ..core.ratelimit import test_shop_limiter
from ..core.reauth import require_reauth
from ..models import StoreTestOrder
from ..services import onvo_test, test_shop

router = APIRouter(prefix="/store/pruebas", tags=["tienda-pruebas"])
REAUTH_MSG = "Confirme con su contraseña de administrador"


def admin_real(p: Principal = Depends(get_principal)) -> Principal:
    """Solo un administrador de verdad (no una vista previa ni un rol con permisos prestados). Si no: 404."""
    if p.role != "admin" or not p.can("settings", "configurar") or p.preview_by is not None or p.is_external:
        raise HTTPException(404, "Not Found")
    return p


def _orders(db: Session, p: Principal) -> list[StoreTestOrder]:
    return list(db.scalars(select(StoreTestOrder).where(StoreTestOrder.tenant_id == p.tenant.id).order_by(StoreTestOrder.id.desc()).limit(test_shop.KEEP)))


@router.get("")
def home(p: Principal = Depends(admin_real), db: Session = Depends(get_db)):
    from ..services import hacienda_prueba as hp

    rows = _orders(db, p)
    if any(test_shop.expire_lazy(o) for o in rows):
        db.commit()
    cfg = test_shop.config(p.tenant)
    out = {
        "status": onvo_test.status(db, p.tenant),
        "config": {"methods": cfg["methods"], "price": str(cfg["price"]), "product": cfg["product"], "quote": str(test_shop.quote(cfg["price"], 1)[3])},
        "orders": [test_shop.out(db, o, with_run=False) for o in rows],
        "hacienda": {"blocked_reason": hp.blocked_reason(db, p.tenant), "cabys_sugerido": hp.cabys_sugerido(db, p.tenant)},
        "limits": {"max_total": "2000", "max_qty": test_shop.MAX_QTY, "max_sessions": test_shop.MAX_SESSIONS},
    }
    db.commit()  # perfil y grupos fiscales que falten se crean al leer
    return out


class MethodsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tarjeta: bool = True
    sinpe_onvo: bool = True


class ConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    methods: MethodsIn
    price: Decimal = Field(gt=0, le=2000)


@router.put("/config")
def put_config(data: ConfigIn, p: Principal = Depends(admin_real), db: Session = Depends(get_db)):
    cfg = test_shop.save_config(db, p.tenant, p.user, data.methods.model_dump(), data.price, ip=p.ip)
    return {"methods": cfg["methods"], "price": str(cfg["price"]), "product": cfg["product"], "quote": str(test_shop.quote(cfg["price"], 1)[3])}


class OrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: str = Field(pattern="^(tarjeta|sinpe_onvo)$")
    quantity: int = Field(1, ge=1, le=test_shop.MAX_QTY)


@router.post("/orders", status_code=201)
def create_order(data: OrderIn, p: Principal = Depends(admin_real), db: Session = Depends(get_db)):
    test_shop_limiter.hit(f"tienda-pruebas:{p.tenant.id}")
    o, url = test_shop.create(db, p.tenant, p.user, data.method, data.quantity, ip=p.ip)
    return {**test_shop.out(db, o), "checkout_url": url}


@router.get("/orders/{oid}")
def order(oid: int, p: Principal = Depends(admin_real), db: Session = Depends(get_db)):
    o = test_shop.get(db, p.tenant, oid)
    if test_shop.expire_lazy(o):
        db.commit()
    return test_shop.out(db, o)


class ConsultaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    regreso: str | None = Field(None, pattern="^(ok|cancelado)$")


@router.post("/orders/{oid}/consultar")
def consult(oid: int, data: ConsultaIn | None = None, p: Principal = Depends(admin_real), db: Session = Depends(get_db)):
    o = test_shop.sync(db, p.tenant, oid, regreso=(data.regreso if data else None))
    return test_shop.out(db, o)


@router.post("/orders/{oid}/reabrir")
def reopen(oid: int, p: Principal = Depends(admin_real), db: Session = Depends(get_db)):
    test_shop_limiter.hit(f"tienda-pruebas:{p.tenant.id}")
    o, url = test_shop.reopen(db, p.tenant, p.user, oid, ip=p.ip)
    return {**test_shop.out(db, o), "checkout_url": url}


@router.post("/orders/{oid}/cancelar")
def cancel(oid: int, p: Principal = Depends(admin_real), db: Session = Depends(get_db)):
    return test_shop.out(db, test_shop.cancel(db, p.tenant, oid))


@router.get("/orders/{oid}/evidencia")
def evidence(oid: int, p: Principal = Depends(admin_real), db: Session = Depends(get_db)):
    ev = test_shop.evidence(db, p.tenant, oid)
    name = f"evidencia-{test_shop.get(db, p.tenant, oid).number}.json"
    body = json.dumps(ev, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    return Response(body, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.delete("/orders")
def clear(p: Principal = Depends(admin_real), db: Session = Depends(get_db)):
    return {"borrados": test_shop.clear(db, p.tenant, p.user, ip=p.ip)}


class FacturaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    admin_password: str | None = Field(None, max_length=200)
    cabys: str = Field("", max_length=20)
    confirmo: bool = False


@router.post("/orders/{oid}/factura-prueba")
def invoice(oid: int, data: FacturaIn, p: Principal = Depends(admin_real), db: Session = Depends(get_db)):
    from ..services import hacienda_prueba as hp

    require_reauth(p, data.admin_password, REAUTH_MSG)
    if not data.confirmo:
        raise HTTPException(422, "Marque «Entiendo que se enviará un comprobante de prueba a Hacienda (ambiente de Pruebas)» para continuar")
    if not secretbox.available():
        raise HTTPException(503, "Falta configurar FISCAL_KEYS en el servidor")
    try:
        o, _run = test_shop.invoice(db, p.tenant, p.user, oid, data.cabys, ip=p.ip)
    except hp.RequisitosFallan as e:
        return JSONResponse(status_code=422, content={"detail": str(e)[:600], "steps": e.steps})
    return test_shop.out(db, o)


@router.post("/orders/{oid}/factura-prueba/consultar")
def invoice_status(oid: int, p: Principal = Depends(admin_real), db: Session = Depends(get_db)):
    """Consulta a Hacienda (un GET de estado, con el freno de hacienda_prueba) SOLO con este POST explicito."""
    from ..services import hacienda_prueba as hp

    o = test_shop.get(db, p.tenant, oid)
    if not o.hacienda_test_run_id:
        raise HTTPException(404, "Este pedido no tiene factura de prueba")
    hp.consultar(db, p.tenant, o.hacienda_test_run_id, p.user.id)
    return test_shop.out(db, test_shop.get(db, p.tenant, oid))
