"""Respaldo portable de la base (o de una empresa) a un .zip verificable. Ver infra/RESPALDO.md.

    uv run python -m app.tools.backup                              # toda la base -> ./respaldos/
    uv run python -m app.tools.backup --tenant crimson             # solo una empresa
    uv run python -m app.tools.backup --salida D:/respaldos/x.zip  # ruta exacta
    uv run python -m app.tools.backup --sin-archivos               # sin los binarios de multimedia (mas liviano)
    uv run python -m app.tools.backup --redactar                   # sin contrasenas ni secretos (para entregar a terceros)
    uv run python -m app.tools.backup --database-url postgresql://...  # otra base que la de DATABASE_URL

Si la base es Postgres y `pg_dump` esta en el PATH, ademas deja al lado un volcado nativo (.pgdump, formato
custom) salvo que se pida --sin-pg-dump. El .zip es el que sirve para cualquier motor; el .pgdump es el plan B.
Sale con codigo 0 y una linea "OK <ruta> sha256=<hash>" que un cron puede revisar.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa
from sqlalchemy.engine import make_url

from .. import models  # noqa: F401  (registra todas las tablas en Base.metadata)
from ..core.db import Base, make_engine
from ..services import backup as bk


def normalize_url(url: str) -> str:
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def pg_dump(url: str, out: Path) -> Path | None:
    exe = shutil.which("pg_dump")
    if not exe:
        print("pg_dump no esta en el PATH: se omite el volcado nativo (el .zip es suficiente para restaurar)")
        return None
    u = make_url(url)
    env = dict(os.environ)
    if u.password:
        env["PGPASSWORD"] = u.password  # nunca en la linea de comandos (se ve en la lista de procesos)
    args = [exe, "-Fc", "--no-owner", "--no-privileges", "-f", str(out), "-d", u.database or ""]
    if u.host:
        args += ["-h", u.host]
    if u.port:
        args += ["-p", str(u.port)]
    if u.username:
        args += ["-U", u.username]
    r = subprocess.run(args, env=env, capture_output=True, text=True, check=False)  # noqa: S603
    if r.returncode != 0:
        print(f"pg_dump fallo (codigo {r.returncode}): {r.stderr.strip()[:400]}", file=sys.stderr)
        return None
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.tools.backup", description="Respaldo portable de Crimson (.zip con JSON por tabla + manifest)")
    ap.add_argument("--tenant", help="slug de la empresa (sin esto: toda la base)")
    ap.add_argument("--salida", help="archivo .zip o carpeta destino (por defecto ./respaldos/)")
    ap.add_argument("--database-url", help="base a respaldar (por defecto DATABASE_URL)")
    ap.add_argument("--sin-archivos", action="store_true", help="no incluir los binarios de multimedia")
    ap.add_argument("--redactar", action="store_true", help="quitar contrasenas, 2FA y secretos cifrados")
    ap.add_argument("--sin-pg-dump", action="store_true", help="no generar el volcado nativo de Postgres")
    a = ap.parse_args(argv)

    if a.database_url:
        url = normalize_url(a.database_url)
    else:
        from ..core.config import settings

        url = settings.database_url
    engine = make_engine(url)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    with engine.connect() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))  # foto consistente
        tenant = None
        if a.tenant:
            t = Base.metadata.tables["tenant"]
            row = conn.execute(sa.select(t).where(t.c.slug == a.tenant)).mappings().first()
            if not row:
                print(f"No existe la empresa '{a.tenant}'", file=sys.stderr)
                return 2
            tenant = SimpleNamespace(**dict(row))
        scope = a.tenant or "todo"
        name = f"crimson-respaldo-{scope}-{stamp}.zip"
        out = Path(a.salida) if a.salida else Path("respaldos")
        if out.suffix.lower() != ".zip":
            out = out / name
        m = bk.write_to_path(conn, Base.metadata, out, tenant=tenant, redact=a.redactar, include_blobs=not a.sin_archivos)
        dialect = conn.dialect.name
    tot = m["totals"]
    print(f"Respaldo: {tot['tables']} tablas, {tot['rows']} filas, {tot['blobs']} archivos ({tot['blob_bytes'] / 1_048_576:.1f} MB)")
    print(f"Migracion: {', '.join(m['alembic_revisions']) or 'sin alembic_version'} - motor: {dialect}")
    if dialect == "postgresql" and not a.sin_pg_dump and not a.tenant:
        dump = pg_dump(url, out.with_suffix(".pgdump"))
        if dump:
            print(f"Volcado nativo: {dump}")
    print(f"OK {out} sha256={m['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
