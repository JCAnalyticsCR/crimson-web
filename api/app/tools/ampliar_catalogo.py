"""Herramienta de una sola vez: crea los productos del catálogo de Eurocomp para las familias
configuradas en auto_catalog, partiendo de los supplier_items ya guardados en la base (sin llamar
a Eurocomp). Imprime conteos por familia y totales.

Uso:
    python -m app.tools.ampliar_catalogo [--dry-run] [--sync] [--tenant-id N]

Flags:
    --dry-run   Muestra qué se crearía sin escribir nada en la base.
    --sync      Descarga primero el catálogo completo de Eurocomp (requiere EUROCOMP_WS_* en el
                entorno) y luego crea. Necesario si la base solo tiene los artículos del filtro
                viejo (antes de la ampliación a 4 familias).
    --tenant-id El id de la empresa. Por defecto la primera empresa activa.

Producción (Railway):
    railway run python -m app.tools.ampliar_catalogo --sync
    railway run python -m app.tools.ampliar_catalogo --dry-run --sync  # primero en seco
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from ..core.db import SessionLocal
from ..models import Product, SupplierItem, Tenant
from ..services import supplier_sync as ss


def _run(tenant_id: int | None, dry_run: bool, sync_first: bool) -> None:
    with SessionLocal() as db:
        # --- empresa objetivo ---
        if tenant_id:
            t = db.get(Tenant, tenant_id)
            if t is None or not t.active:
                print(f"ERROR: empresa {tenant_id} no encontrada o inactiva", file=sys.stderr)
                sys.exit(1)
        else:
            tenants = list(db.scalars(select(Tenant).where(Tenant.active).limit(2)))
            if len(tenants) != 1:
                print("ERROR: especifique --tenant-id (hay 0 o más de 1 empresa activa)", file=sys.stderr)
                sys.exit(1)
            t = tenants[0]
        print(f"Empresa: {t.name!r} (id={t.id})")

        # --- configuración auto_catalog ---
        cfg = ss.ac_settings(t)
        print(f"auto_catalog: enabled={cfg['enabled']}, familias={cfg['families']}, publish_web={cfg['publish_web']}")
        if not cfg.get("enabled"):
            print("AVISO: auto_catalog no está habilitado en esta empresa. Activándolo para esta ejecución...")

        # --- sync opcional: descarga el catálogo completo antes de crear ---
        if sync_first:
            print("\n[--sync] Descargando catálogo completo de Eurocomp...")
            from ..providers.sirett import SirettClient, SirettError
            client = SirettClient.from_settings()
            if client is None:
                print("ERROR: faltan las credenciales de Eurocomp (EUROCOMP_WS_ID, EUROCOMP_WS_PASSWORD o EUROCOMP_WS_KEY)", file=sys.stderr)
                sys.exit(1)
            try:
                items, received = ss.fetch(client)
                print(f"  Recibidos de Eurocomp: {received}, útiles con el filtro nuevo: {len(items)}")
                out_sync = ss.apply(db, t, items, received=received)
                db.commit()
                ss.trim_memory()
                print(f"  Aplicado: {out_sync.get('nuevo', 0)} nuevos, {out_sync.get('actualizado', 0)} actualizados, {out_sync.get('inactivar', 0)} inactivados")
            except SirettError as e:
                print(f"ERROR al descargar de Eurocomp: {e.message}", file=sys.stderr)
                sys.exit(1)

        # --- estadísticas por familia antes de crear ---
        sup = ss.find_supplier(db, t.id)
        if sup is None:
            print("\nNo hay proveedor Eurocomp registrado para esta empresa.", file=sys.stderr)
            sys.exit(1)

        families = list(cfg.get("families") or ss.AC_DEFAULT["families"])
        print("\nArtículos activos del proveedor por familia (sin producto vinculado):")
        for fam in families:
            sin_prod = db.scalar(
                select(__import__("sqlalchemy", fromlist=["func"]).func.count()).select_from(SupplierItem).where(
                    SupplierItem.tenant_id == t.id,
                    SupplierItem.supplier_id == sup.id,
                    SupplierItem.active,
                    SupplierItem.category == fam,
                    SupplierItem.product_id.is_(None),
                )
            )
            con_prod = db.scalar(
                select(__import__("sqlalchemy", fromlist=["func"]).func.count()).select_from(SupplierItem).where(
                    SupplierItem.tenant_id == t.id,
                    SupplierItem.supplier_id == sup.id,
                    SupplierItem.active,
                    SupplierItem.category == fam,
                    SupplierItem.product_id.is_not(None),
                )
            )
            print(f"  {fam}: {sin_prod} sin producto, {con_prod} ya vinculados")

        # --- activar temporalmente si está deshabilitado ---
        enabled_orig = cfg.get("enabled", False)
        if not enabled_orig:
            # activar solo para esta ejecución (en memoria, sin commit)
            t.settings = {**(t.settings or {})}
            ss.ac_save(t, {"enabled": True})

        # --- ejecutar en seco o de verdad ---
        mode = "DRY-RUN" if dry_run else "CREANDO"
        print(f"\n[{mode}] auto_catalog_run...")
        result = ss.auto_catalog_run(db, t, sup, batch_size=50, dry_run=dry_run, commit_batches=not dry_run)

        if not enabled_orig and not dry_run:
            # restablecer enabled=False después de la ejecución
            db.refresh(t) if not dry_run else None

        # --- resumen ---
        print("\nResultado:")
        print(f"  Creados:               {result.get('creados', 0)}")
        print(f"  Ya existían (borrados): {result.get('skip_borrado', 0)}")
        print(f"  Sin costo (saltados):  {result.get('skip_sin_costo', 0)}")
        print(f"  Hikvision destacados:  {result.get('destacados_hik', 0)}")
        print(f"  Publicados en tienda:  {result.get('publicados', 0)}")
        if dry_run:
            print("  [dry-run: nada fue escrito en la base]")

        # --- conteo final de productos ---
        if not dry_run:
            total_prods = db.scalar(
                select(__import__("sqlalchemy", fromlist=["func"]).func.count()).select_from(Product).where(
                    Product.tenant_id == t.id, Product.active, Product.item_type == "producto"
                )
            )
            print(f"\nTotal productos activos en la empresa: {total_prods}")
        ss.trim_memory()


def main() -> None:
    parser = argparse.ArgumentParser(description="Amplía el catálogo de Crimson con los artículos de Eurocomp")
    parser.add_argument("--dry-run", action="store_true", help="Mostrar conteos sin crear nada")
    parser.add_argument("--sync", action="store_true", help="Descargar catálogo de Eurocomp primero")
    parser.add_argument("--tenant-id", type=int, default=None, help="ID de la empresa (por defecto la única activa)")
    args = parser.parse_args()
    _run(args.tenant_id, args.dry_run, args.sync)


if __name__ == "__main__":
    main()
