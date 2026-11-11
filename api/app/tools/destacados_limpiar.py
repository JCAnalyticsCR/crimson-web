"""Quitar el «destacado web» (O3-08), una sola vez y SOLO despues de que el dueno reconfirme el cambio del 21/10.

    python -m app.tools.destacados_limpiar simular --empresa crimson --salida destacados.json
    python -m app.tools.destacados_limpiar aplicar --empresa crimson --informe destacados.json --bitacora bitacora.json
    python -m app.tools.destacados_limpiar deshacer --empresa crimson --bitacora bitacora.json

Solo cambia Product.web_featured. Los reemplaza «Fijar arriba en su categoria» (8 como maximo, PUT /store/content/pins).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("accion", choices=("simular", "aplicar", "deshacer"))
    ap.add_argument("--empresa", required=True)
    ap.add_argument("--salida", type=Path)
    ap.add_argument("--informe", type=Path)
    ap.add_argument("--bitacora", type=Path)
    a = ap.parse_args(argv)

    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Tenant
    from app.services import store_ranking, store_showcase

    with SessionLocal() as db:
        t = db.scalar(select(Tenant).where(Tenant.slug == a.empresa))
        if t is None:
            print(f"No existe la empresa {a.empresa}", file=sys.stderr)
            return 2
        if a.accion == "simular":
            rep = store_ranking.featured_simulate(db, t.id)
            if a.salida:
                a.salida.write_text(json.dumps(rep, indent=1), encoding="utf-8")
            print(f"{rep['count']} productos destacados. Nada se guardo.")
            return 0
        if a.accion == "aplicar":
            if not a.informe or not a.bitacora:
                print("aplicar necesita --informe y --bitacora", file=sys.stderr)
                return 2
            out = store_ranking.featured_apply(db, t.id, json.loads(a.informe.read_text(encoding="utf-8")))
            a.bitacora.write_text(json.dumps(out, indent=1), encoding="utf-8")
            store_showcase.bump_catalog_version(db, t)
            db.commit()
            print(f"{len(out['cleared'])} productos sin destacado. Bitacora: {a.bitacora}")
            return 0
        if not a.bitacora:
            print("deshacer necesita --bitacora", file=sys.stderr)
            return 2
        n = store_ranking.featured_undo(db, t.id, json.loads(a.bitacora.read_text(encoding="utf-8")))
        store_showcase.bump_catalog_version(db, t)
        db.commit()
        print(f"{n} productos vuelven a estar destacados.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
