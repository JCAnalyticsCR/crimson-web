"""Restaura un respaldo .zip de app.tools.backup en una base VACIA y verifica conteos y hashes.

    uv run python -m app.tools.restore respaldos/crimson-respaldo-todo-20261012-031500.zip --target sqlite:///./prueba.db
    uv run python -m app.tools.restore respaldo.zip --target postgresql://usuario:clave@host:5432/crimson_prueba

Reglas:
- Se niega si la base destino tiene datos (cualquier fila en cualquier tabla del sistema). Nunca mezcla.
- Si la base destino no tiene tablas, las crea con el modelo actual y marca la migracion del respaldo.
  Si ya tiene el esquema (alembic upgrade head), la migracion debe coincidir con la del respaldo.
  --permitir-version-distinta lo intenta igual (solo si las columnas son compatibles).
- Primero verifica el hash de cada archivo del zip; luego inserta todo en UNA transaccion respetando el orden
  de llaves foraneas; al final re-lee el destino y compara conteos y hash de cada tabla. Codigo 0 = identico.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

import sqlalchemy as sa

from .. import models  # noqa: F401  (registra todas las tablas en Base.metadata)
from ..core.db import Base, make_engine
from ..services import backup as bk
from .backup import normalize_url


def code_heads() -> list[str]:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        ini = Path(__file__).resolve().parents[2] / "alembic.ini"
        cfg = Config(str(ini))
        cfg.set_main_option("script_location", str(ini.parent / "alembic"))
        return sorted(ScriptDirectory.from_config(cfg).get_heads())
    except Exception:  # noqa: BLE001
        return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.tools.restore", description="Restaura un respaldo de Crimson en una base vacia")
    ap.add_argument("zip", help="archivo .zip generado por app.tools.backup")
    ap.add_argument("--target", required=True, help="DATABASE_URL de la base destino (vacia)")
    ap.add_argument("--permitir-version-distinta", action="store_true", help="restaurar aunque la migracion no coincida")
    a = ap.parse_args(argv)

    src = Path(a.zip)
    if not src.is_file():
        print(f"No existe {src}", file=sys.stderr)
        return 2
    try:
        z = zipfile.ZipFile(src)
        m = bk.read_manifest(z)
        bk.verify_zip(z, m)
    except (bk.BackupError, zipfile.BadZipFile) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3
    tot = m["totals"]
    print(f"Respaldo {m['created_at']} - alcance {m['scope']}{' (' + m['tenant']['slug'] + ')' if m.get('tenant') else ''} - {tot['rows']} filas - hash verificado")
    if m.get("redacted"):
        print("AVISO: respaldo SIN secretos: los usuarios deberan restablecer contrasena y 2FA; las pasarelas, sus llaves.")
    if not m.get("blobs_included", True):
        print("AVISO: respaldo sin archivos de multimedia: las imagenes y PDF quedaran vacios.")

    engine = make_engine(normalize_url(a.target))
    try:
        return _restaurar(engine, z, m, a.permitir_version_distinta)
    finally:
        # sin conexiones abiertas al terminar: el cron de respaldo restaura aqui su verificacion y tiene que salir limpio
        engine.dispose()
        z.close()


def _restaurar(engine, z: zipfile.ZipFile, m: dict, permitir: bool) -> int:
    md = Base.metadata
    revs = m.get("alembic_revisions") or []
    heads = code_heads()
    with engine.connect() as conn:
        insp = sa.inspect(conn)
        has_schema = insp.has_table("tenant")
        if has_schema:
            busy = {t: n for t, n in bk.target_counts(conn, md).items() if n}
            if busy:
                t, n = next(iter(busy.items()))
                print(f"ERROR: la base destino NO esta vacia ({t} tiene {n} filas; {len(busy)} tablas con datos). Restaure solo en una base nueva.", file=sys.stderr)
                return 4
            current = bk.alembic_revisions(conn)
            if revs and current and sorted(current) != sorted(revs) and not permitir:
                print(f"ERROR: el destino esta en la migracion {current} y el respaldo en {revs}. Use la misma version del codigo o --permitir-version-distinta.", file=sys.stderr)
                return 5
        else:
            if revs and heads and sorted(revs) != heads and not permitir:
                print(f"ERROR: el respaldo es de la migracion {revs} y este codigo esta en {heads}. Haga checkout de la version del respaldo o use --permitir-version-distinta.", file=sys.stderr)
                return 5
            md.create_all(conn)
            stamp = revs or heads
            if stamp:
                conn.execute(sa.text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
                for r in stamp:
                    conn.execute(sa.text("INSERT INTO alembic_version (version_num) VALUES (:r)"), {"r": r})
            conn.commit()
            print(f"Esquema creado en el destino (migracion marcada: {', '.join(stamp) or 'ninguna'})")
        if conn.in_transaction():
            conn.commit()
        try:
            with conn.begin():
                bk.restore(conn, md, z, m)
        except (bk.BackupError, sa.exc.SQLAlchemyError) as e:
            print(f"ERROR al restaurar (no quedo nada a medias): {e}", file=sys.stderr)
            return 6
        problems = bk.verify_restored(conn, md, m)
    if problems:
        print("VERIFICACION FALLIDA:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 7
    print(f"OK restaurado y verificado: {len(m['tables'])} tablas, {m['totals']['rows']} filas, hashes identicos al respaldo ({m['sha256'][:16]}...)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
