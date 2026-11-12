"""Traslado de los archivos de multimedia (tabla media) de Postgres al Storage Bucket de Railway, y de vuelta.

    python -m app.tools.media_al_bucket simular                     # cuenta archivos y bytes por estado y prueba el bucket
    python -m app.tools.media_al_bucket copiar [--lote 50]          # sube, RELEE y verifica; marca "s3"; NO borra nada
    python -m app.tools.media_al_bucket liberar                     # vista previa de lo que vaciaria (no escribe)
    python -m app.tools.media_al_bucket liberar --si                # vacia media.data de las filas copiadas y verificadas
    python -m app.tools.media_al_bucket revertir                    # baja del bucket a la base las filas "s3" y las marca "db"

Opciones: --tenant <slug> (una empresa), --lote N (filas por transaccion), --limite N (tope de filas en esta corrida),
--releer (liberar: vuelve a bajar cada archivo y compara el sha256, ademas del tamano), --database-url, --resumen <json>.

Estados de una fila:
    pendiente  storage=db, con bytes        -> copiar lo sube
    copiado    storage=s3, con bytes        -> se lee del bucket; la base guarda todavia la copia (liberar la vacia)
    liberado   storage=s3, sin bytes        -> solo en el bucket (el respaldo diario lo baja y lo mete al zip)
    vacio      storage=db, sin bytes        -> anomalia: no deberia existir (se reporta)

Garantias:
- copiar verifica ANTES (los bytes de la base contra su sha256), sube con el cuerpo firmado y RELEE el objeto: solo
  marca "s3" si lo que devuelve el bucket tiene el mismo sha256 y tamano. Nunca toca media.data.
- liberar solo toma filas "copiado" (es decir, ya verificadas por copiar), confirma con HEAD (o --releer) que el objeto
  sigue ahi con el tamano correcto, y pide --si. Sin --si no escribe nada.
- Idempotente y reanudable: cada lote es una transaccion; volver a correr retoma lo que falte. Un error en una fila no
  detiene la corrida: se anota (id y motivo, sin nombres de archivo) y se sigue.
- revertir no borra objetos del bucket (quedan como copia; se borran a mano o con el bucket).

Siempre escribe un resumen .json (por defecto media_al_bucket-<modo>-<fecha UTC>.json en la carpeta actual).
Codigos de salida: 0 bien · 1 configuracion · 2 hubo filas con error (lo demas quedo hecho) · 3 liberar sin --si.
Despues de liberar, el espacio en Postgres se devuelve con VACUUM FULL media (bloquea la tabla) o pg_repack: ver
infra/RESPALDO.md seccion 8.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from .. import models  # noqa: F401  (registra las tablas)
from ..core.db import Base, make_engine
from ..services import media_store
from ..services.bucket_s3 import S3Client, S3Error

OK, CONFIG, CON_ERRORES, SIN_CONFIRMAR = 0, 1, 2, 3
MODOS = ("simular", "copiar", "liberar", "revertir")

media = Base.metadata.tables["media"]
c = media.c


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _mb(n: int) -> str:
    return f"{(n or 0) / 1_048_576:.1f} MB"


ESTADOS = {
    "pendiente": sa.and_(c.storage == "db", c.data.is_not(None)),
    "copiado": sa.and_(c.storage == "s3", c.data.is_not(None)),
    "liberado": sa.and_(c.storage == "s3", c.data.is_(None)),
    "vacio": sa.and_(c.storage == "db", c.data.is_(None)),
}


class Corrida:
    def __init__(self, modo: str, tenant_id: int | None, lote: int, limite: int | None):
        self.modo, self.tenant_id, self.lote, self.limite = modo, tenant_id, max(1, lote), limite
        self.hechos = 0
        self.bytes = 0
        self.saltados = 0
        self.errores: list[dict] = []
        self.inicio = datetime.now(UTC)

    def filtro(self, *conds):
        if self.tenant_id is not None:
            conds = (*conds, c.tenant_id == self.tenant_id)
        return sa.and_(*conds)

    def error(self, mid: int, motivo: str) -> None:
        self.errores.append({"id": mid, "motivo": motivo})
        print(f"  ERROR media.id={mid}: {motivo}", file=sys.stderr)

    def tope(self) -> bool:
        return self.limite is not None and self.hechos + len(self.errores) + self.saltados >= self.limite


def contar(conn, corrida: Corrida) -> dict:
    out = {}
    for nombre, cond in ESTADOS.items():
        n, b = conn.execute(sa.select(sa.func.count(), sa.func.coalesce(sa.func.sum(c.size), 0)).where(corrida.filtro(cond))).one()
        out[nombre] = {"archivos": int(n), "bytes": int(b)}
    return out


def probar_bucket(cli: S3Client) -> str:
    """HEAD de una llave que no existe: 404 = credenciales y bucket bien. No escribe nada."""
    try:
        cli.head(f"media/_sonda/{secrets.token_hex(8)}")
        return "ok"
    except S3Error as e:
        return f"falla ({e.status} {e.code})".strip()


def _lotes(conn_factory, corrida: Corrida, cond):
    """Ids por lote en orden, con cursor (id > ultimo): una fila con error no se reintenta en la misma corrida."""
    ultimo = 0
    while not corrida.tope():
        with conn_factory() as conn:
            ids = list(conn.scalars(sa.select(c.id).where(corrida.filtro(cond, c.id > ultimo)).order_by(c.id).limit(corrida.lote)))
        if not ids:
            return
        ultimo = ids[-1]
        if corrida.limite is not None:
            ids = ids[: max(0, corrida.limite - (corrida.hechos + len(corrida.errores) + corrida.saltados))]
        yield ids


def copiar(engine, cli: S3Client, corrida: Corrida) -> None:
    for ids in _lotes(engine.connect, corrida, ESTADOS["pendiente"]):
        with engine.begin() as conn:
            for mid in ids:
                r = conn.execute(sa.select(c.id, c.tenant_id, c.key, c.content_type, c.size, c.sha256, c.data).where(c.id == mid, ESTADOS["pendiente"])).first()
                if r is None:  # la borraron o la copio otra corrida
                    corrida.saltados += 1
                    continue
                data = bytes(r.data)
                if len(data) != r.size or _sha(data) != r.sha256:
                    corrida.error(mid, "los bytes de la base no coinciden con su sha256/tamano (no se copia)")
                    continue
                okey = media_store.object_key(r.tenant_id, r.key)
                try:
                    cli.put(okey, data, r.content_type)
                    back = cli.get(okey)
                except S3Error as e:
                    corrida.error(mid, f"bucket {e.status} {e.code}".strip())
                    continue
                if len(back) != len(data) or _sha(back) != r.sha256:
                    corrida.error(mid, "la relectura del bucket no coincide (no se marca)")
                    continue
                conn.execute(media.update().where(c.id == mid, c.storage == "db").values(storage="s3"))
                corrida.hechos += 1
                corrida.bytes += len(data)
        print(f"  lote hasta media.id={ids[-1] if ids else '-'}: {corrida.hechos} copiados ({_mb(corrida.bytes)}), {len(corrida.errores)} con error")


def liberar(engine, cli: S3Client, corrida: Corrida, releer: bool) -> None:
    for ids in _lotes(engine.connect, corrida, ESTADOS["copiado"]):
        with engine.begin() as conn:
            for mid in ids:
                r = conn.execute(sa.select(c.id, c.tenant_id, c.key, c.size, c.sha256).where(c.id == mid, ESTADOS["copiado"])).first()
                if r is None:
                    corrida.saltados += 1
                    continue
                okey = media_store.object_key(r.tenant_id, r.key)
                try:
                    if releer:
                        back = cli.get(okey)
                        bien = len(back) == r.size and _sha(back) == r.sha256
                    else:
                        h = cli.head(okey)
                        bien = h is not None and h["size"] == r.size
                except S3Error as e:
                    corrida.error(mid, f"bucket {e.status} {e.code}".strip())
                    continue
                if not bien:
                    corrida.error(mid, "el objeto falta en el bucket o no coincide (no se libera; vuelva a correr copiar tras revertir)")
                    continue
                res = conn.execute(media.update().where(c.id == mid, c.storage == "s3", c.data.is_not(None)).values(data=None))
                if res.rowcount:
                    corrida.hechos += 1
                    corrida.bytes += r.size or 0
        print(f"  lote hasta media.id={ids[-1] if ids else '-'}: {corrida.hechos} liberados ({_mb(corrida.bytes)}), {len(corrida.errores)} con error")


def revertir(engine, cli: S3Client, corrida: Corrida) -> None:
    for ids in _lotes(engine.connect, corrida, c.storage == "s3"):
        with engine.begin() as conn:
            for mid in ids:
                r = conn.execute(sa.select(c.id, c.tenant_id, c.key, c.size, c.sha256, c.data.is_not(None).label("tiene")).where(c.id == mid, c.storage == "s3")).first()
                if r is None:
                    corrida.saltados += 1
                    continue
                if r.tiene:  # copiado sin liberar: la base ya tiene los bytes
                    conn.execute(media.update().where(c.id == mid, c.storage == "s3").values(storage="db"))
                    corrida.hechos += 1
                    continue
                try:
                    back = cli.get(media_store.object_key(r.tenant_id, r.key))
                except S3Error as e:
                    corrida.error(mid, f"bucket {e.status} {e.code}".strip())
                    continue
                if len(back) != r.size or _sha(back) != r.sha256:
                    corrida.error(mid, "el objeto del bucket no coincide con su sha256/tamano (no se baja)")
                    continue
                conn.execute(media.update().where(c.id == mid, c.storage == "s3").values(data=back, storage="db"))
                corrida.hechos += 1
                corrida.bytes += len(back)
        print(f"  lote hasta media.id={ids[-1] if ids else '-'}: {corrida.hechos} devueltos a la base ({_mb(corrida.bytes)}), {len(corrida.errores)} con error")


def _url(a) -> str:
    if a.database_url:
        from .backup import normalize_url

        return normalize_url(a.database_url)
    from ..core.config import settings

    return settings.database_url


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.tools.media_al_bucket", description="Multimedia de Postgres al Storage Bucket (y de vuelta)")
    ap.add_argument("modo", choices=MODOS)
    ap.add_argument("--tenant", help="slug de la empresa (sin esto: todas)")
    ap.add_argument("--lote", type=int, default=50, help="filas por transaccion (por defecto 50)")
    ap.add_argument("--limite", type=int, help="tope de filas en esta corrida")
    ap.add_argument("--si", action="store_true", help="liberar: confirma que se vacian los bytes de la base")
    ap.add_argument("--releer", action="store_true", help="liberar: baja cada objeto y compara el sha256 (mas lento)")
    ap.add_argument("--database-url", help="base (por defecto DATABASE_URL)")
    ap.add_argument("--resumen", help="archivo .json del resumen")
    a = ap.parse_args(argv)

    engine = make_engine(_url(a))
    try:
        tenant_id = None
        if a.tenant:
            t = Base.metadata.tables["tenant"]
            with engine.connect() as conn:
                tenant_id = conn.scalar(sa.select(t.c.id).where(t.c.slug == a.tenant))
            if tenant_id is None:
                print(f"No existe la empresa '{a.tenant}'", file=sys.stderr)
                return CONFIG
        corrida = Corrida(a.modo, tenant_id, a.lote, a.limite)
        with engine.connect() as conn:
            antes = contar(conn, corrida)
        print("Estado: " + " · ".join(f"{k} {v['archivos']} ({_mb(v['bytes'])})" for k, v in antes.items()))
        if antes["vacio"]["archivos"]:
            print(f"AVISO: {antes['vacio']['archivos']} filas sin bytes en la base ni en el bucket (storage=db, data NULL)", file=sys.stderr)

        cli = media_store.client() if media_store.configured() else None
        bucket = probar_bucket(cli) if cli else "sin configurar (BUCKET, ENDPOINT, ACCESS_KEY_ID, SECRET_ACCESS_KEY)"
        print(f"Bucket: {bucket}")
        codigo = OK
        if a.modo != "simular":
            if bucket != "ok":
                print("FALLO: el bucket no esta disponible; no se hace nada", file=sys.stderr)
                return CONFIG
            if a.modo == "copiar":
                copiar(engine, cli, corrida)
            elif a.modo == "liberar":
                if not a.si:
                    print(
                        f"Vista previa: liberar vaciaria {antes['copiado']['archivos']} archivos ({_mb(antes['copiado']['bytes'])}) de la base. "
                        "Agregue --si para hacerlo."
                    )
                    codigo = SIN_CONFIRMAR
                else:
                    liberar(engine, cli, corrida, a.releer)
            elif a.modo == "revertir":
                from ..core.config import settings

                if settings.media_storage == "s3":
                    print("AVISO: MEDIA_STORAGE=s3 sigue activo: las subidas nuevas siguen yendo al bucket (ponga MEDIA_STORAGE=db)", file=sys.stderr)
                revertir(engine, cli, corrida)
        with engine.connect() as conn:
            despues = contar(conn, corrida)
        if corrida.errores and codigo == OK:
            codigo = CON_ERRORES
        resumen = {
            "modo": a.modo,
            "confirmado": bool(a.si) if a.modo == "liberar" else None,
            "tenant": a.tenant,
            "inicio": corrida.inicio.isoformat(),
            "fin": datetime.now(UTC).isoformat(),
            "bucket": bucket,
            "antes": antes,
            "despues": despues,
            "hechos": corrida.hechos,
            "bytes": corrida.bytes,
            "saltados": corrida.saltados,
            "errores": corrida.errores,
            "codigo": codigo,
        }
        ruta = Path(a.resumen) if a.resumen else Path(f"media_al_bucket-{a.modo}-{corrida.inicio.strftime('%Y%m%d-%H%M%S')}.json")
        ruta.write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Despues: " + " · ".join(f"{k} {v['archivos']} ({_mb(v['bytes'])})" for k, v in despues.items()))
        if a.modo == "liberar" and a.si and corrida.hechos:
            print(f"Liberados {corrida.hechos} archivos ({_mb(corrida.bytes)}). Para devolver el espacio: VACUUM FULL media; o pg_repack -t media")
        print(f"{'OK' if codigo == OK else 'TERMINO CON AVISOS'} {a.modo}: {corrida.hechos} filas, {len(corrida.errores)} con error · resumen en {ruta}")
        return codigo
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
