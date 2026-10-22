"""Proceso hijo: refresco de existencias de Eurocomp cada 6h (suppliers.eurocomp_stock).

Se invoca con: python -m app.tools.eurocomp_stock
El worker lo llama con subprocess.run para que el SO recupere toda la RAM al terminar.
Imprime una linea JSON al final con el resumen; el worker la lee como resultado de la tarea.
Guarda el pico de memoria (ru_maxrss) en el estado y rearma el snapshot.
"""
from __future__ import annotations

import json
import sys

try:
    import resource as _res

    def _peak_kb() -> int:
        ru = _res.getrusage(_res.RUSAGE_SELF)
        return int(ru.ru_maxrss if sys.platform != "darwin" else ru.ru_maxrss / 1024)

except ImportError:
    def _peak_kb() -> int:
        return 0


def main() -> None:
    from app.core.db import SessionLocal
    from app.services import supplier_sync

    with SessionLocal() as db:
        out = supplier_sync.refresh_linked(db)

    peak_kb = _peak_kb()

    # guardar el pico de memoria en el estado
    with SessionLocal() as db:
        for t in supplier_sync.targets(db):
            supplier_sync._save_state(t, last_stock_peak_mem_kb=peak_kb)
        db.commit()

    # rearmar el snapshot: las existencias cambiaron
    with SessionLocal() as db:
        for t in supplier_sync.targets(db):
            try:
                from app.services import store_snapshot

                store_snapshot.rebuild(db, t.id)
            except Exception as exc:  # noqa: BLE001
                import logging

                logging.getLogger("crimson.tools").warning("snapshot stock (empresa %s): %s", t.id, exc)

    print(json.dumps({str(k): v for k, v in out.items()} | {"peak_mem_kb": peak_kb}))


if __name__ == "__main__":
    main()
