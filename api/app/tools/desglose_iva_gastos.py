"""Relleno del desglose de IVA por tarifa de los gastos que ya existen (reunion con la contadora, 10/11/2026).

Recalcula expense_tax_line de los gastos con comprobante electronico guardado (XML del proveedor) y, con --manuales,
tambien de los gastos a mano (una sola parte con su tarifa). Las reglas son las de services/expense_tax.

Uso (por defecto SIMULA: muestra lo que haria y no escribe nada):
    python -m app.tools.desglose_iva_gastos                       # todas las empresas, solo gastos con XML
    python -m app.tools.desglose_iva_gastos --empresa 1           # una empresa (id)
    python -m app.tools.desglose_iva_gastos --manuales            # tambien los gastos sin XML
    python -m app.tools.desglose_iva_gastos --aplicar             # escribe (una transaccion por lote)

Idempotente: lo que ya esta igual no se toca, asi que correrlo dos veces no cambia nada la segunda vez.
Nunca cambia los montos del gasto: solo su desglose.
"""

from __future__ import annotations

import argparse
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Expense, Tenant
from ..services import expense_tax


def run(db: Session, tenant_id: int | None = None, *, apply: bool = False, manuales: bool = False, lote: int = 200) -> dict:
    """Recorre los gastos y devuelve el resumen. apply=False: simula (rollback de todo lo calculado)."""
    q = select(Expense.id).order_by(Expense.id)
    if tenant_id is not None:
        q = q.where(Expense.tenant_id == tenant_id)
    if not manuales:
        q = q.where(Expense.received_document_id.is_not(None))
    ids = list(db.scalars(q))
    res: Counter = Counter()
    tarifas: Counter = Counter()
    errores: list[dict] = []
    for i in range(0, len(ids), lote):
        for eid in ids[i : i + lote]:
            e = db.get(Expense, eid)
            if e is None:
                continue
            res["revisados"] += 1
            try:
                parts = expense_tax.compute(db, e)
                old = expense_tax.stored_parts(db, e)
                if old == parts:
                    res["sin_cambio"] += 1
                    continue
                res["nuevos" if not old else "actualizados"] += 1
                if any(p.source == "xml" for p in parts) and len(parts) > 1:
                    res["con_varias_tarifas"] += 1
                for p in parts:
                    tarifas[p.label] += 1
                if apply:
                    with db.begin_nested():
                        expense_tax.sync(db, e)
            except Exception as ex:  # noqa: BLE001 - se informa y se sigue con el resto
                errores.append({"expense_id": eid, "motivo": str(ex)[:200]})
        if apply:
            db.commit()
        else:
            db.rollback()
    return {**res, "tarifas": dict(tarifas), "errores": errores, "aplicado": apply}


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(prog="python -m app.tools.desglose_iva_gastos", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--empresa", type=int, help="id de la empresa (tenant); por defecto todas")
    ap.add_argument("--aplicar", action="store_true", help="escribe el desglose (sin esto solo simula)")
    ap.add_argument("--manuales", action="store_true", help="incluye los gastos sin XML (una parte con su tarifa)")
    ap.add_argument("--lote", type=int, default=200, help="gastos por transaccion (por defecto 200)")
    args = ap.parse_args(argv)
    from ..core.db import SessionLocal

    code = 0
    with SessionLocal() as db:
        q = select(Tenant).order_by(Tenant.id)
        if args.empresa:
            q = q.where(Tenant.id == args.empresa)
        for t in db.scalars(q).all():
            r = run(db, t.id, apply=args.aplicar, manuales=args.manuales, lote=args.lote)
            print(f"== Empresa {t.id} · {t.name} · {'APLICADO' if r['aplicado'] else 'simulación (use --aplicar para escribir)'}")
            print(
                f"  revisados {r.get('revisados', 0)} · sin cambio {r.get('sin_cambio', 0)} · nuevos {r.get('nuevos', 0)}"
                f" · actualizados {r.get('actualizados', 0)} · con varias tarifas {r.get('con_varias_tarifas', 0)}"
            )
            if r["tarifas"]:
                print("  partes por tarifa: " + ", ".join(f"{k}: {v}" for k, v in sorted(r["tarifas"].items())))
            for err in r["errores"]:
                print(f"  error · gasto {err['expense_id']}: {err['motivo']}")
                code = 2
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
