"""Importa el catalogo publico de Fygaro al catalogo propio y lo publica en la tienda.

    uv run python -m app.tools.import_fygaro fygaro_catalog.json                 # vista previa (no escribe)
    uv run python -m app.tools.import_fygaro fygaro_catalog.json --apply \\
        --images-dir ./fygaro/images --report reporte.json                     # aplica

Por defecto es un DRY-RUN: muestra que crearia/actualizaria y los casos dudosos (precio vacio, emparejado por
nombre, precio distinto al del sistema, SKU repetido...). Solo `--apply` escribe, y en una sola transaccion:
si algo falla a la mitad no queda nada a medias. Correrlo dos veces no duplica (ver app/services/fygaro_import.py).

La base es la de DATABASE_URL. OJO en produccion: PUBLIC_BASE_URL debe ser la URL publica del portal, porque las
fotos quedan guardadas con esa direccion (…/api/media/f/<llave>).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

from ..core.config import settings
from ..core.db import SessionLocal
from ..models import Tenant
from ..services import fygaro_import


def _tenant(db, slug: str | None) -> Tenant:
    if slug:
        t = db.scalar(select(Tenant).where(Tenant.slug == slug))
        if not t:
            sys.exit(f"No existe la empresa '{slug}'")
        return t
    ts = db.scalars(select(Tenant).where(Tenant.active)).all()
    if len(ts) != 1:
        sys.exit(f"Hay {len(ts)} empresas activas: indique --tenant <slug>")
    return ts[0]


def _print(report: dict) -> None:
    s = report["summary"]
    mode = "APLICADO" if s["aplicado"] else "VISTA PREVIA (no se escribio nada; use --apply)"
    print(f"\n== Importacion Fygaro · {mode} ==")
    print(
        f"{s['total']} productos · nuevos {s['nuevo']} · actualizar {s['actualizar']} · sin cambios {s['sin_cambios']}"
        f" · omitidos {s['omitir']} · errores {s['error']}"
    )
    print(
        f"dudosos {s['dudosos']} · por nombre {s['por_nombre']} · sin precio {s['sin_precio']} · precio distinto {s['precio_distinto']}"
        f" · sin CABYS {s['sin_cabys']} · fotos {s['fotos']} · monedas {', '.join(s['monedas']) or '-'}"
    )
    print(f"terminos y privacidad de la tienda: {s['legal']}")
    if s["aplicado"]:
        print(f"fotos subidas {s.get('images_uploaded', 0)} · reutilizadas {s.get('images_reused', 0)} · no encontradas {s.get('images_missing', 0)}")
    rows = report["rows"]
    for title, pick in (
        ("Errores y omitidos", lambda r: r["action"] in ("error", "omitir")),
        ("Dudosos (revisar antes de aplicar)", lambda r: r["doubtful"] and r["action"] not in ("error", "omitir")),
    ):
        sel = [r for r in rows if pick(r)]
        if not sel:
            continue
        print(f"\n-- {title}: {len(sel)}")
        for r in sel[:200]:
            print(f"  [{r['action']:<11}] {r['code'] or '-':<24} {r['name'][:60]:<60} · {' | '.join(r['warnings'])}")
    print("\n-- Muestra de cambios")
    for r in [r for r in rows if r["action"] in ("nuevo", "actualizar")][:15]:
        extra = f"{r['currency']} {r['price']} · IVA {r['tax_rate']} % · {r['category'] or 'sin categoria'} · {r['images']} foto(s)"
        print(f"  [{r['action']:<11}] {r['code'] or '-':<24} {r['name'][:50]:<50} · {extra} · {', '.join(r['changes'])}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.tools.import_fygaro", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("catalog", type=Path, help="JSON del relevamiento de Fygaro")
    ap.add_argument("--tenant", help="slug de la empresa (por defecto la unica activa)")
    ap.add_argument("--apply", action="store_true", help="escribir en la base (sin esto es vista previa)")
    ap.add_argument("--images-dir", type=Path, help="carpeta con las fotos ya descargadas (nombre = el del archivo en Fygaro)")
    ap.add_argument("--no-download", action="store_true", help="no descargar fotos que falten en --images-dir")
    ap.add_argument("--update-prices", action="store_true", help="tambien cambiar el precio de productos que ya existian")
    ap.add_argument("--report", type=Path, help="guardar el reporte completo en JSON")
    args = ap.parse_args(argv)

    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict) or not isinstance(catalog.get("products"), list):
        sys.exit("El JSON no tiene la forma esperada: {categories: [...], products: [...]}")
    if args.apply and settings.env != "local" and "localhost" in settings.public_base_url:
        sys.exit("PUBLIC_BASE_URL apunta a localhost en un entorno compartido: las fotos quedarian con URLs rotas")

    with SessionLocal() as db:
        tenant = _tenant(db, args.tenant)
        report = fygaro_import.run(
            db,
            tenant,
            catalog,
            apply=args.apply,
            images_dir=args.images_dir,
            download_images=not args.no_download,
            update_prices=args.update_prices,
        )
        if args.apply:
            db.commit()
        else:
            db.rollback()
    _print(report)
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(f"\nReporte completo: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
