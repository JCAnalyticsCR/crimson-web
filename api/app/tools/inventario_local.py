"""Arranque del inventario local (reunion 21/10, item 11): archiva los productos viejos sin existencia que vinieron de
la tienda anterior y asegura la bodega "Bodega principal" (de venta) para cargar los equipos fisicos.

Que se archiva (services/inventory_cleanup): productos del importador de la tienda anterior (Fygaro) con existencia
propia en cero, sin vinculo al catalogo de un proveedor y sin reservas ni pedidos web abiertos. Archivar no borra:
salen de listas, busquedas y tienda, conservan su historia y se restauran desde Archivo.

Uso (por defecto solo muestra, no escribe nada):
    python -m app.tools.inventario_local                       # todas las empresas activas
    python -m app.tools.inventario_local --empresa 1
    python -m app.tools.inventario_local --empresa 1 --aplicar
    python -m app.tools.inventario_local --empresa 1 --aplicar --sin-predeterminada   # no cambia la bodega predeterminada
Despues, los equipos se cargan en Inventarios -> Ajuste de inventario -> Entrada (Bodega principal).
NO correr contra produccion sin revisar antes la vista previa con quien administra el inventario.
"""

from __future__ import annotations

import argparse

from sqlalchemy import select

from ..models import Tenant
from ..services import inventory_cleanup as ic


def main() -> None:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(prog="python -m app.tools.inventario_local", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--empresa", type=int, help="id de la empresa (tenant); por defecto todas las activas")
    ap.add_argument("--aplicar", action="store_true", help="archiva y ajusta la bodega (sin esto solo muestra)")
    ap.add_argument("--sin-predeterminada", action="store_true", help="no marca Bodega principal como predeterminada")
    args = ap.parse_args()
    make_default = not args.sin_predeterminada

    from ..core.db import SessionLocal

    with SessionLocal() as db:
        q = select(Tenant).where(Tenant.id == args.empresa) if args.empresa else select(Tenant).where(Tenant.active)
        for t in db.scalars(q).all():
            print(f"== Empresa {t.id} · {t.name}")
            plan = ic.main_warehouse_plan(db, t.id, make_default)
            print(f"  {ic.MAIN_NAME}: {plan['accion']}" + (f" ({', '.join(plan['cambios'])})" if plan.get("cambios") else ""))
            cands = ic.candidates(db, t)
            archivar = [c for c in cands if c["archivar"]]
            print(f"  productos de la tienda anterior: {len(cands)} · se archivan {len(archivar)}")
            for c in cands:
                print(f"    {'ARCHIVAR' if c['archivar'] else 'se deja '} · {c['code']} · {c['name']} · {c['motivo']}")
            if not args.aplicar:
                continue
            ic.ensure_main_warehouse(db, t.id, None, make_default)
            n = ic.archive_legacy(db, t)
            db.commit()
            print(f"  aplicado: {ic.MAIN_NAME} lista, {n} productos archivados")


if __name__ == "__main__":  # pragma: no cover
    main()
