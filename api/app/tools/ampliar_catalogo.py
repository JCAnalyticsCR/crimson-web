"""Herramienta de una sola vez: crea los productos del catálogo de Eurocomp para las familias
configuradas en auto_catalog, partiendo de los supplier_items ya guardados en la base (sin llamar
a Eurocomp). Imprime conteos por familia y totales.

Uso:
    python -m app.tools.ampliar_catalogo [--dry-run] [--sync] [--activar] [--publicar] [--tenant-id N]

Flags:
    --dry-run   Muestra qué se crearía sin escribir nada en la base.
    --sync      Descarga primero el catálogo completo de Eurocomp (requiere EUROCOMP_WS_* en el
                entorno) y luego crea. La descarga filtra al vuelo (keep=relevant) y el proveedor
                solo devuelve ~1650 artículos útiles; trim_memory() se llama después de cada lote.
    --activar   Guarda auto_catalog {enabled: true, families: las 4} en la base antes de crear,
                de forma que las sincronizaciones automáticas futuras lo respeten.
    --publicar  Como --activar, pero además publish_web=true (los productos nuevos quedan visibles
                en la tienda al crearse). Implica --activar.
    --tenant-id El id de la empresa. Por defecto la primera empresa activa.

Producción (Railway):
    railway run python -m app.tools.ampliar_catalogo --dry-run --sync        # primero en seco
    railway run python -m app.tools.ampliar_catalogo --activar --publicar --sync  # ejecución real
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from ..core.db import SessionLocal
from ..models import Product, SupplierItem, Tenant
from ..services import supplier_sync as ss


def _run(tenant_id: int | None, dry_run: bool, sync_first: bool, activar: bool, publicar: bool) -> None:
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

        # --- --activar / --publicar: persiste la configuración ANTES de crear ---
        # (implica que las sincronizaciones automáticas futuras también usarán estos valores)
        if activar or publicar:
            new_cfg: dict = {
                "enabled": True,
                "families": list(ss.AC_DEFAULT["families"]),
                "publish_web": bool(publicar),
            }
            ss.ac_save(t, new_cfg)
            if not dry_run:
                db.commit()
            print(
                f"[{'--activar' if not publicar else '--publicar'}] Configuración guardada:"
                f" enabled=True, familias={new_cfg['families']}, publish_web={new_cfg['publish_web']}"
            )
            if dry_run:
                print("  [dry-run: el commit de configuración fue omitido]")
            else:
                print("  Esta configuración quedará activa en futuras sincronizaciones automáticas.")

        # --- configuración auto_catalog (ya actualizada si se usó --activar/--publicar) ---
        cfg = ss.ac_settings(t)
        print(f"auto_catalog: enabled={cfg['enabled']}, familias={cfg['families']}, publish_web={cfg['publish_web']}")
        if not cfg.get("enabled") and not (activar or publicar):
            print("AVISO: auto_catalog no está habilitado en esta empresa. Activándolo para esta ejecución...")

        # --- sync opcional: descarga el catálogo completo antes de crear ---
        # fetch() filtra al vuelo (keep=relevant), manteniendo en memoria solo ~1650 artículos útiles
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

        # --- activar temporalmente en memoria si todavía no está habilitado (sin --activar/--publicar) ---
        enabled_orig = cfg.get("enabled", False)
        if not enabled_orig and not (activar or publicar):
            # solo para esta ejecución: no se persiste (el admin decide si lo deja activo)
            ss.ac_save(t, {"enabled": True})

        # --- ejecutar en seco o de verdad ---
        mode = "DRY-RUN" if dry_run else "CREANDO"
        print(f"\n[{mode}] auto_catalog_run...")
        result = ss.auto_catalog_run(db, t, sup, batch_size=50, dry_run=dry_run, commit_batches=not dry_run)

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
    parser.add_argument("--sync", action="store_true", help="Descargar catálogo de Eurocomp primero (filtra al vuelo)")
    parser.add_argument("--activar", action="store_true", help="Persiste auto_catalog {enabled: true, familias: las 4} antes de crear")
    parser.add_argument("--publicar", action="store_true", help="Como --activar pero además publish_web=true (implica --activar)")
    parser.add_argument("--tenant-id", type=int, default=None, help="ID de la empresa (por defecto la única activa)")
    args = parser.parse_args()
    _run(args.tenant_id, args.dry_run, args.sync, args.activar or args.publicar, args.publicar)


if __name__ == "__main__":
    main()
