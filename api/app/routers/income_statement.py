"""Contabilidad -> Contable -> Resultados: estado de resultados mensual (services/income_statement.py).

- Ver: accounting.ver (admin, contabilidad; la lectura temporal de soporte tambien).
- Excel: ademas accounting.exportar, y queda en la bitacora.
- Costo de inventario (kardex): solo quien ve costos (Principal.sees_costs, catalog.costos). Al resto le llega null.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..services import income_statement as ist
from ..services.documents import audit

router = APIRouter(prefix="/accounting", tags=["contabilidad"])

MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _months(from_: str | None, to: str | None):
    from ..services.invoice_states import cr_today

    hoy = cr_today()
    for v in (from_, to):
        if v is not None and not MONTH.match(v):
            raise HTTPException(422, "Mes inválido (AAAA-MM)")
    b = ist.parse_month(to) if to else hoy.replace(day=1)
    a = ist.parse_month(from_) if from_ else b.replace(month=1)
    if a > b:
        raise HTTPException(422, "El mes inicial es posterior al final")
    if len(ist.month_range(a, b)) > ist.MAX_MONTHS:
        raise HTTPException(422, f"El rango máximo es de {ist.MAX_MONTHS} meses")
    return a, b


@router.get("/income-statement")
def income_statement(
    from_: str | None = Query(None, alias="from", description="Primer mes, AAAA-MM (por defecto, enero del año del último mes)"),
    to: str | None = Query(None, description="Último mes, AAAA-MM (por defecto, el mes en curso)"),
    format: str = Query("json", pattern="^(json|xlsx)$"),  # noqa: A002
    p: Principal = Depends(require("accounting", "ver")),
    db: Session = Depends(get_db),
):
    a, b = _months(from_, to)
    data = ist.build(db, p.tenant.id, a, b, p.sees_costs)
    if format == "xlsx":
        if not p.can("accounting", "exportar"):
            raise HTTPException(403, "Tu rol no puede exportar la contabilidad")
        xlsx = ist.to_xlsx(data, p.tenant.name)
        audit(
            db,
            p.tenant.id,
            p.user.id,
            "export",
            "report",
            None,
            {"report": "estado_resultados_mensual", "format": "xlsx", "from": data["from"], "to": data["to"]},
            ip=p.ip,
        )
        db.commit()
        return Response(
            xlsx,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="estado-resultados-{data["from"]}-a-{data["to"]}.xlsx"'},
        )
    return data
