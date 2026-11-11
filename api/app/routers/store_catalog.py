"""Panel «Contenido por aprobar» (O3-11): lotes de borradores del contenido de la tienda.

Generar y descartar: catalog.editar. Aprobar, registrar la muestra, aplicar y deshacer: catalog.publicar (solo el
administrador): nada de lo propuesto llega a la tienda sin su aprobacion. Ver services/catalog_drafts.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..services import catalog_drafts as cd
from ..services.documents import audit

router = APIRouter(prefix="/store/content", tags=["tienda"])


class GenerateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(pattern="^(web_name|web_subtitle|web_type|web_specs|web_description)$")
    category_id: int | None = None
    product_ids: list[int] | None = Field(None, max_length=500)
    published_only: bool = True
    simulate: bool = True


class RowsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_ids: list[int] | None = Field(None, max_length=5000)  # None = todo el lote


class SampleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checked: list[int] = Field(max_length=5000)
    wrong: list[int] = Field(default_factory=list, max_length=5000)


def _run(fn):
    try:
        return fn()
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except cd.DraftError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/fields")
def fields(p: Principal = Depends(require("catalog", "ver"))):
    return [{"field": k, "label": v, "ready": k in cd.GENERATORS} for k, v in cd.FIELDS.items()]


@router.get("/batches")
def batches(p: Principal = Depends(require("catalog", "ver")), db: Session = Depends(get_db)):
    return cd.batches_out(db, p.tenant.id)


@router.get("/batches/{bid}")
def batch(bid: int, p: Principal = Depends(require("catalog", "ver")), db: Session = Depends(get_db)):
    return _run(lambda: cd.drafts_out(db, p.tenant.id, bid))


@router.post("/generate")
def generate(data: GenerateIn, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    """simulate=True (por defecto): solo la vista previa. False: crea el lote en borrador (no cambia la tienda)."""
    out = _run(
        lambda: cd.generate(
            db,
            p.tenant,
            data.field,
            category_id=data.category_id,
            product_ids=data.product_ids,
            simulate=data.simulate,
            user_id=p.user.id,
            published_only=data.published_only,
        )
    )
    if not data.simulate and out.get("batch_id"):
        audit(db, p.tenant.id, p.user.id, "generate", "catalog_batch", out["batch_id"], {"campo": data.field, **out["counts"]}, ip=p.ip)
        db.commit()
    elif not data.simulate:
        db.commit()
    return out


@router.post("/batches/{bid}/approve")
def approve(bid: int, data: RowsIn, p: Principal = Depends(require("catalog", "publicar")), db: Session = Depends(get_db)):
    n = _run(lambda: cd.decide(db, p.tenant.id, bid, "aprobado", data.draft_ids, p.user.id))
    audit(db, p.tenant.id, p.user.id, "approve", "catalog_batch", bid, {"filas": n}, ip=p.ip)
    db.commit()
    return _run(lambda: cd.drafts_out(db, p.tenant.id, bid))


@router.post("/batches/{bid}/discard")
def discard(bid: int, data: RowsIn, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    n = _run(lambda: cd.decide(db, p.tenant.id, bid, "descartado", data.draft_ids, p.user.id))
    audit(db, p.tenant.id, p.user.id, "discard", "catalog_batch", bid, {"filas": n}, ip=p.ip)
    db.commit()
    return _run(lambda: cd.drafts_out(db, p.tenant.id, bid))


@router.get("/batches/{bid}/sample")
def sample(bid: int, p: Principal = Depends(require("catalog", "ver")), db: Session = Depends(get_db)):
    """las filas que hay que revisar a mano (al azar, siempre las mismas para el lote)"""
    return {"draft_ids": _run(lambda: cd.pick_sample(db, p.tenant.id, bid))}


@router.post("/batches/{bid}/sample")
def record_sample(bid: int, data: SampleIn, p: Principal = Depends(require("catalog", "publicar")), db: Session = Depends(get_db)):
    st = _run(lambda: cd.record_sample(db, p.tenant.id, bid, data.checked, data.wrong, p.user.id))
    audit(db, p.tenant.id, p.user.id, "sample", "catalog_batch", bid, st, ip=p.ip)
    db.commit()
    return st


@router.post("/batches/{bid}/apply")
def apply(bid: int, p: Principal = Depends(require("catalog", "publicar")), db: Session = Depends(get_db)):
    out = _run(lambda: cd.apply(db, p.tenant, bid, p.user.id))
    audit(db, p.tenant.id, p.user.id, "apply", "catalog_batch", bid, out, ip=p.ip)
    db.commit()
    return out


@router.post("/batches/{bid}/undo")
def undo(bid: int, p: Principal = Depends(require("catalog", "publicar")), db: Session = Depends(get_db)):
    out = _run(lambda: cd.undo(db, p.tenant, bid, p.user.id))
    audit(db, p.tenant.id, p.user.id, "undo", "catalog_batch", bid, {"deshechos": out["deshechos"], "cambiados": len(out["cambiados_despues"])}, ip=p.ip)
    db.commit()
    return out
