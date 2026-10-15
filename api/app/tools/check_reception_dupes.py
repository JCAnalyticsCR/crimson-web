"""Antes de desplegar la migracion de facturacion: lista recepciones duplicadas por (empresa, clave). Solo lectura.

Uso:  python -m app.tools.check_reception_dupes
Las copias exactas sin respuesta las fusiona la migracion sola; si hay dos con respuesta o gasto, o XML distinto, la
migracion se detiene: resolver esas a mano (descartar la copia o desligar el gasto) antes de desplegar.
"""

from __future__ import annotations

import sqlalchemy as sa


def find(conn) -> list[dict]:
    out = []
    for tid, clave, _n in conn.execute(sa.text("SELECT tenant_id, clave, COUNT(*) FROM received_document GROUP BY tenant_id, clave HAVING COUNT(*) > 1")).all():
        rows = conn.execute(
            sa.text("SELECT id, xml_document, action, expense_id FROM received_document WHERE tenant_id = :t AND clave = :c ORDER BY id"), {"t": tid, "c": clave}
        ).all()
        vivas = [r for r in rows if r.action is not None or r.expense_id is not None]
        out.append({"tenant_id": tid, "ids": [r.id for r in rows], "auto": len(vivas) <= 1 and len({r.xml_document for r in rows}) == 1})
    return out


def main() -> None:  # pragma: no cover - CLI
    from ..core.db import engine

    with engine.connect() as conn:
        res = find(conn)
    if not res:
        print("Sin duplicados")
        return
    for r in res:
        print(f"empresa {r['tenant_id']}: ids {r['ids']} -> {'se fusionan solos' if r['auto'] else 'RESOLVER A MANO'}")


if __name__ == "__main__":  # pragma: no cover
    main()
