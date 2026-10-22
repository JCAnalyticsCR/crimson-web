"""Reportes visuales (reunion 2, pedidos 1 y 3): el estado de resultados completo (JSON y Excel con formato, en
colones o en dolares) y el tablero de indicadores. Rutas de dos segmentos: no chocan con /reports/{key} (ops.py).

- Estado de resultados: el mismo permiso que el reporte "resultados" (accounting.ver: el admin y contabilidad).
- Tablero: reports.ver + ver las ventas de toda la empresa (sales.ver_todo). El margen solo con catalog.costos.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..services import profit_loss, report_board
from ..services.documents import audit

router = APIRouter(tags=["reportes"])


def _range(from_: date | None, to: date | None) -> tuple[date, date]:
    from ..services.invoice_states import cr_today

    b = to or cr_today()
    a = from_ or b.replace(day=1)
    if a > b:
        raise HTTPException(422, "La fecha inicial es posterior a la final")
    if (b - a).days > 366 * 5:
        raise HTTPException(422, "El rango máximo es de 5 años")
    return a, b


@router.get("/reports/resultados/completo")
def profit_and_loss(
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    currency: str = Query("CRC", pattern="^(CRC|USD)$"),
    format: str = Query("json", pattern="^(json|xlsx)$"),  # noqa: A002
    p: Principal = Depends(require("reports", "ver")),
    db: Session = Depends(get_db),
):
    from .ops import report_allowed

    if not report_allowed(p, "resultados"):
        raise HTTPException(403, "Este reporte no está disponible para su rol")
    a, b = _range(from_, to)
    data = profit_loss.build(db, p.tenant.id, a, b, currency)
    if format == "xlsx":
        if not p.can("reports", "exportar"):
            raise HTTPException(403, "Su rol no puede exportar reportes")
        xlsx = profit_loss.to_xlsx(data, p.tenant.name)
        audit(
            db,
            p.tenant.id,
            p.user.id,
            "export",
            "report",
            None,
            {"report": "resultados", "format": "xlsx", "from": str(a), "to": str(b), "currency": currency},
            ip=p.ip,
        )
        db.commit()
        return Response(
            xlsx,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="estado-resultados-{a}-{b}-{currency}.xlsx"'},
        )
    data["can_register_commissions"] = bool(data["comisiones_sin_gasto"]) and p.can("commissions", "aprobar") and p.can("accounting", "crear")
    return data


@router.get("/reports/tablero/indicadores")
def dashboard(
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    p: Principal = Depends(require("reports", "ver")),
    db: Session = Depends(get_db),
):
    if not p.sees_all_sales:
        raise HTTPException(403, "El tablero muestra las ventas de toda la empresa: no está disponible para su rol")
    a, b = _range(from_, to)
    out = report_board.board(db, p.tenant.id, a, b, p.sees_costs)
    out["can_pnl"] = p.can("accounting", "ver")
    return out
