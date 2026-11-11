"""Limpieza de una sola vez de las marcas ya guardadas (O3-02): simulacion, aplicar con bitacora y deshacer.

Solo cambia Product.brand (no es fiscal: la factura no la lleva). Nunca toca el codigo, el nombre, precios ni
existencias. Flujo:
1. simulate(): el informe de lo que cambiaria (marca de hoy -> marca oficial, cuantos productos) sin escribir nada.
2. El dueno revisa y aprueba ese informe.
3. apply(informe): aplica EXACTAMENTE lo del informe, y solo en productos cuya marca sigue siendo la del informe
   (si alguien la cambio entretanto, se salta). Devuelve la bitacora (producto, antes, despues).
4. undo(bitacora): devuelve cada producto a la marca de antes, si nadie la cambio despues.
CLI: python -m app.tools.marcas_canonicas (ver ese archivo).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Product
from .brands import canonical_brand


def simulate(db: Session, tid: int) -> dict:
    changes: list[dict] = []
    groups: dict[tuple[str, str], int] = {}
    for pid, brand in db.execute(select(Product.id, Product.brand).where(Product.tenant_id == tid, Product.brand.is_not(None)).order_by(Product.id)):
        new = canonical_brand(brand)
        if new and new != brand:
            changes.append({"product_id": pid, "before": brand, "after": new})
            groups[(brand, new)] = groups.get((brand, new), 0) + 1
    return {
        "tenant_id": tid,
        "at": datetime.now(UTC).isoformat(),
        "changes": changes,
        "summary": [{"before": b, "after": a, "products": n} for (b, a), n in sorted(groups.items(), key=lambda x: (-x[1], x[0]))],
    }


def apply(db: Session, tid: int, report: dict, user_id: int | None = None) -> dict:
    if report.get("tenant_id") != tid:
        raise ValueError("El informe es de otra empresa")
    want = {int(c["product_id"]): c for c in report.get("changes") or []}
    log, skipped = [], []
    prods = db.scalars(select(Product).where(Product.tenant_id == tid, Product.id.in_(list(want) or [0])))
    for p in prods:
        c = want[p.id]
        if p.brand != c["before"]:
            skipped.append(p.id)
            continue
        p.brand = c["after"]
        log.append({"product_id": p.id, "before": c["before"], "after": c["after"]})
    db.flush()
    return {"tenant_id": tid, "at": datetime.now(UTC).isoformat(), "by": user_id, "log": log, "skipped": skipped}


def undo(db: Session, tid: int, journal: dict) -> dict:
    if journal.get("tenant_id") != tid:
        raise ValueError("La bitacora es de otra empresa")
    rows = {int(x["product_id"]): x for x in journal.get("log") or []}
    undone, changed = 0, []
    for p in db.scalars(select(Product).where(Product.tenant_id == tid, Product.id.in_(list(rows) or [0]))):
        x = rows[p.id]
        if p.brand != x["after"]:
            changed.append(p.id)
            continue
        p.brand = x["before"]
        undone += 1
    db.flush()
    return {"undone": undone, "changed_since": changed}
