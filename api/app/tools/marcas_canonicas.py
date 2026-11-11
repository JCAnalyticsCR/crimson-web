"""Marcas oficiales en los productos ya guardados (O3-02), una sola vez y con aprobacion del dueno.

    python -m app.tools.marcas_canonicas simular --empresa crimson --salida informe.json
    python -m app.tools.marcas_canonicas aplicar --empresa crimson --informe informe.json --bitacora bitacora.json
    python -m app.tools.marcas_canonicas deshacer --empresa crimson --bitacora bitacora.json

simular no escribe nada. aplicar exige el informe que aprobo el dueno y solo cambia lo que dice (Product.brand); deja
la bitacora para deshacer. Nunca toca codigo, nombre fiscal, precios ni existencias. Se corre contra la base que diga
DATABASE_URL: probar primero en local.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("accion", choices=("simular", "aplicar", "deshacer"))
    ap.add_argument("--empresa", required=True, help="slug de la empresa")
    ap.add_argument("--salida", type=Path, help="simular: donde guardar el informe")
    ap.add_argument("--informe", type=Path, help="aplicar: el informe aprobado")
    ap.add_argument("--bitacora", type=Path, help="aplicar: donde guardarla; deshacer: la que se aplico")
    a = ap.parse_args(argv)

    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Tenant
    from app.services import brand_cleanup, store_showcase

    with SessionLocal() as db:
        t = db.scalar(select(Tenant).where(Tenant.slug == a.empresa))
        if t is None:
            print(f"No existe la empresa {a.empresa}", file=sys.stderr)
            return 2
        if a.accion == "simular":
            rep = brand_cleanup.simulate(db, t.id)
            text = json.dumps(rep, ensure_ascii=False, indent=1)
            if a.salida:
                a.salida.write_text(text, encoding="utf-8")
            for s in rep["summary"]:
                print(f"{s['products']:5d}  {s['before']!r} -> {s['after']!r}")
            print(f"{len(rep['changes'])} productos cambiarian. Nada se guardo.")
            return 0
        if a.accion == "aplicar":
            if not a.informe or not a.bitacora:
                print("aplicar necesita --informe (el aprobado) y --bitacora", file=sys.stderr)
                return 2
            out = brand_cleanup.apply(db, t.id, json.loads(a.informe.read_text(encoding="utf-8")))
            a.bitacora.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
            store_showcase.bump_catalog_version(db, t)
            db.commit()
            print(f"{len(out['log'])} productos con la marca oficial; {len(out['skipped'])} saltados (cambiaron). Bitacora: {a.bitacora}")
            return 0
        if not a.bitacora:
            print("deshacer necesita --bitacora", file=sys.stderr)
            return 2
        out = brand_cleanup.undo(db, t.id, json.loads(a.bitacora.read_text(encoding="utf-8")))
        store_showcase.bump_catalog_version(db, t)
        db.commit()
        print(f"{out['undone']} productos volvieron a su marca anterior; {len(out['changed_since'])} cambiaron despues y no se tocaron.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
