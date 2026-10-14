"""Bandeja de Aprobaciones (ver services/approvals.py). Solo lectura: aprobar se hace en cada pantalla."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..services import approvals as svc

router = APIRouter(tags=["aprobaciones"])


@router.get("/approvals")
def approvals(limit: int = Query(20, ge=1, le=50), p: Principal = Depends(require("approvals", "ver")), db: Session = Depends(get_db)):
    """Secciones que este usuario puede aprobar (aunque esten en 0), lo mas antiguo primero."""
    return svc.full(db, p, limit)


@router.get("/approvals/summary")
def approvals_summary(p: Principal = Depends(require("approvals", "ver")), db: Session = Depends(get_db)):
    """Solo conteos, para el numero del menu."""
    return svc.summary(db, p)
