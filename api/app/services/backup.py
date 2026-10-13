"""Respaldo portable y restauracion (documento de Andres, secciones 16 y 18).

Formato: un .zip con
  manifest.json          version de migracion, fecha, alcance, conteos y hash de cada archivo + hash global
  data/<tabla>.json      filas de cada tabla en orden de llave primaria, con valores canonicos
  blobs/<tabla>/<pk>_<columna>   binarios (los archivos de multimedia viven en la tabla media como bytea)
  media_index.csv        lista legible de archivos (nombre, tipo, tamano, sha256)

No depende de pg_dump: funciona igual sobre SQLite y Postgres, y se puede restaurar de uno a otro. Los valores se
serializan de forma canonica (fechas en UTC sin zona, decimales sin ceros de mas, JSON con llaves ordenadas) para
que el hash de una tabla sea el mismo antes y despues de restaurar, sin importar el motor.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import zipfile
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.sql.ddl import sort_tables_and_constraints

FORMAT = "crimson-backup"
FORMAT_VERSION = 1
# sesiones: no se respaldan nunca (tras restaurar todos vuelven a iniciar sesion)
SKIP_TABLES = {"refresh_token", "alembic_version"}
# tablas sin dueno: en un respaldo de UNA empresa entran completas (son datos de referencia)
GLOBAL_FULL = {"role", "exchange_rate"}
# tablas sin dueno que en un respaldo de empresa solo entran si algo de la empresa las referencia
GLOBAL_REFERENCED = {"user"}
# tablas que no se pueden atribuir a una empresa: solo en el respaldo completo
GLOBAL_ONLY_FULL = {"webhook_event"}
# secretos que NO salen en la descarga del portal (si salen en el respaldo de la base, que es para recuperar)
# (los NOT NULL / unicos llevan un marcador por fila: la copia se puede restaurar, pero nadie entra con ella)
def _mark(r: dict) -> str:
    return f"!redactado-{r.get('id')}"


REDACT_COLUMNS = {
    "user": {"password_hash": _mark, "totp_secret": None, "totp_recovery": [], "totp_enabled": False},
    "invitation": {"token_hash": _mark},
    "payment_gateway_config": {"secret_encrypted": None},
    "api_credential": {"secret_hash": _mark, "secret_encrypted": _mark},
}
REDACT_JSON_SUFFIXES = ("_enc", "_encrypted", "password", "secret", "token")


class BackupError(Exception):
    pass


# ---------- serializacion canonica ----------
def _dec(v: Decimal) -> str:
    s = format(v, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "", "-") else s


def _dt(v: datetime) -> str:
    if v.tzinfo is not None:
        v = v.astimezone(UTC).replace(tzinfo=None)
    return v.isoformat()


def encode(v):
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return v
    if isinstance(v, Decimal):
        return _dec(v)
    if isinstance(v, datetime):
        return _dt(v)
    if isinstance(v, (date, time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"$b64": base64.b64encode(bytes(v)).decode()}
    if isinstance(v, (dict, list)):
        return json.loads(json.dumps(v, default=str, sort_keys=True))
    return str(v)


def decode(v, col: sa.Column):
    if v is None:
        return None
    t = col.type
    if isinstance(t, sa.DateTime):
        dt = datetime.fromisoformat(v)
        return dt.replace(tzinfo=UTC) if getattr(t, "timezone", False) and dt.tzinfo is None else dt
    if isinstance(t, sa.Date):
        return date.fromisoformat(v)
    if isinstance(t, sa.Time):
        return time.fromisoformat(v)
    if isinstance(t, sa.Float):
        return float(v)
    if isinstance(t, sa.Numeric):
        return Decimal(str(v))
    if isinstance(t, sa.Boolean):
        return bool(v)
    if isinstance(t, sa.Integer):
        return int(v)
    if isinstance(v, dict) and "$b64" in v and isinstance(t, sa.LargeBinary):
        return base64.b64decode(v["$b64"])
    return v


def _dump(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _redact_json(v):
    if isinstance(v, dict):
        return {k: (None if any(k.lower().endswith(s) for s in REDACT_JSON_SUFFIXES) else _redact_json(x)) for k, x in v.items()}
    if isinstance(v, list):
        return [_redact_json(x) for x in v]
    return v


# ---------- orden de tablas y FKs ciclicas ----------
def ordered_tables(metadata: sa.MetaData) -> tuple[list[sa.Table], dict[str, set[str]]]:
    """Tablas en orden de dependencias + columnas que se insertan en NULL y se completan al final
    (FKs ciclicas como invoice.quote_id <-> quote.converted_invoice_id, y auto-referencias como category.parent_id)."""
    res = sort_tables_and_constraints(metadata.tables.values())
    tables = [t for t, _ in res if t is not None]
    deferred: dict[str, set[str]] = defaultdict(set)
    for t, fkcs in res:
        if t is None:
            for fk in fkcs:
                deferred[fk.parent.name].update(fk.column_keys)
    for t in tables:
        for fk in t.foreign_key_constraints:
            if fk.referred_table is t:
                deferred[t.name].update(fk.column_keys)
    for tname, cols in deferred.items():
        for c in cols:
            if not metadata.tables[tname].c[c].nullable:
                raise BackupError(f"FK ciclica no anulable: {tname}.{c}")
    return tables, deferred


def _pk(t: sa.Table) -> list[sa.Column]:
    pk = list(t.primary_key.columns)
    return pk or list(t.columns)


def _blob_cols(t: sa.Table) -> list[str]:
    return [c.name for c in t.columns if isinstance(c.type, sa.LargeBinary)]


def alembic_revisions(conn: Connection) -> list[str]:
    try:
        if not sa.inspect(conn).has_table("alembic_version"):
            return []
        return sorted(r[0] for r in conn.execute(sa.text("SELECT version_num FROM alembic_version")))
    except Exception:  # noqa: BLE001
        return []


# ---------- seleccion de filas por alcance ----------
def _owned(metadata: sa.MetaData, tables: list[sa.Table]) -> set[str]:
    """Tablas que pertenecen a una empresa: tienen tenant_id o cuelgan (por FK) de una que lo tiene."""
    owned = {"tenant"}
    changed = True
    while changed:
        changed = False
        for t in tables:
            if t.name in owned or t.name in SKIP_TABLES or t.name in GLOBAL_FULL | GLOBAL_REFERENCED | GLOBAL_ONLY_FULL:
                continue
            if "tenant_id" in t.c or any(fk.referred_table.name in owned for fk in t.foreign_key_constraints):
                owned.add(t.name)
                changed = True
    return owned


def _select_rows(conn: Connection, t: sa.Table, where=None, cols: list[sa.Column] | None = None):
    q = sa.select(*(cols or list(t.columns)))
    if where is not None:
        q = q.where(where)
    return conn.execute(q.order_by(*_pk(t))).mappings().all()


def plan_rows(conn: Connection, metadata: sa.MetaData, tenant_id: int | None) -> dict[str, list[dict]]:
    """Filas a respaldar por tabla (sin los binarios: esos se leen uno por uno al escribir el zip)."""
    tables, _ = ordered_tables(metadata)
    out: dict[str, list[dict]] = {}
    nonblob = {t.name: [c for c in t.columns if c.name not in _blob_cols(t)] for t in tables}
    if tenant_id is None:
        for t in tables:
            if t.name in SKIP_TABLES:
                continue
            out[t.name] = [dict(r) for r in _select_rows(conn, t, cols=nonblob[t.name])]
        return out
    owned = _owned(metadata, tables)
    ids: dict[tuple[str, str], set] = defaultdict(set)  # (tabla, columna referida) -> valores incluidos

    referenced = {(fk.column.table.name, fk.column.name) for tt in tables for fk in tt.foreign_keys}

    def remember(t: sa.Table, rows: list[dict]):
        for c in t.columns:
            if (t.name, c.name) in referenced:
                ids[(t.name, c.name)].update(r[c.name] for r in rows if r.get(c.name) is not None)

    for t in tables:
        if t.name not in owned:
            continue
        if t.name == "tenant":
            rows = _select_rows(conn, t, t.c.id == tenant_id, nonblob[t.name])
        elif "tenant_id" in t.c:
            rows = _select_rows(conn, t, t.c.tenant_id == tenant_id, nonblob[t.name])
        else:
            # hija sin tenant_id (lineas, puntos, materiales): entra si su padre de la empresa entro
            rows = []
            parents = [fk for fk in t.foreign_keys if fk.column.table.name in owned]
            for r in _select_rows(conn, t, cols=nonblob[t.name]):
                if any(r[fk.parent.name] is not None and r[fk.parent.name] in ids[(fk.column.table.name, fk.column.name)] for fk in parents):
                    rows.append(r)
        rows = [dict(r) for r in rows]
        out[t.name] = rows
        remember(t, rows)
    # globales referenciadas (usuarios): solo los que la empresa usa
    for gname in GLOBAL_REFERENCED:
        g = metadata.tables[gname]
        need: dict[str, set] = defaultdict(set)
        for t in tables:
            if t.name not in out:
                continue
            for fk in t.foreign_keys:
                if fk.column.table is g:
                    need[fk.column.name].update(r[fk.parent.name] for r in out[t.name] if r.get(fk.parent.name) is not None)
        rows = []
        for col, vals in need.items():
            if vals:
                rows += [dict(r) for r in _select_rows(conn, g, g.c[col].in_(sorted(vals)), nonblob[gname])]
        uniq = {tuple(r[c.name] for c in _pk(g)): r for r in rows}
        out[gname] = [uniq[k] for k in sorted(uniq)]
    for gname in GLOBAL_FULL:
        out[gname] = [dict(r) for r in _select_rows(conn, metadata.tables[gname], cols=nonblob[gname])]
    return out


# ---------- escritura del zip ----------
def _table_doc(t: sa.Table, columns: list[str], rows: list[list]) -> bytes:
    return _dump({"table": t.name, "columns": columns, "rows": rows})


def serialize_table(t: sa.Table, rows: Iterable[dict], columns: list[str], blobs: dict | None, redact: bool) -> tuple[bytes, list]:
    """Filas -> documento JSON canonico. blobs: {(pk,col): (sha, size)} o None si los binarios no se incluyen."""
    bcols = set(_blob_cols(t))
    pkc = [c.name for c in _pk(t)]
    red = REDACT_COLUMNS.get(t.name, {}) if redact else {}
    out_rows = []
    for r in rows:
        vals = []
        key = "-".join(str(r[c]) for c in pkc)
        for c in columns:
            if c in bcols:
                if blobs is None:
                    vals.append({"$blob": None})
                else:
                    s, n = blobs[(key, c)]
                    vals.append({"$blob": f"blobs/{t.name}/{key}_{c}", "sha256": s, "size": n} if s else None)
                continue
            v = (red[c](r) if callable(red[c]) else red[c]) if c in red else r.get(c)
            if redact and isinstance(t.c[c].type, sa.JSON):
                v = _redact_json(v)
            vals.append(encode(v))
        out_rows.append(vals)
    return _table_doc(t, columns, out_rows), out_rows


def write_backup(conn: Connection, metadata: sa.MetaData, dest, *, tenant=None, redact: bool = False, include_blobs: bool = True, dialect: str | None = None) -> dict:
    """Escribe el zip en dest (ruta o archivo binario abierto). Devuelve el manifest."""
    tid = tenant.id if tenant is not None else None
    if "tenant" not in metadata.tables:
        raise BackupError("Metadata vacia: falta importar app.models")
    rows_by_table = plan_rows(conn, metadata, tid)
    tables, _ = ordered_tables(metadata)
    files: dict[str, str] = {}
    manifest_tables: dict[str, dict] = {}
    media_rows = []
    blob_count = blob_bytes = 0
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for t in tables:
            if t.name not in rows_by_table:
                continue
            rows = rows_by_table[t.name]
            columns = [c.name for c in t.columns]
            bcols = _blob_cols(t)
            blobs = None
            if bcols and include_blobs:
                blobs = {}
                pkc = [c.name for c in _pk(t)]
                for r in rows:
                    key = "-".join(str(r[c]) for c in pkc)
                    cond = sa.and_(*(t.c[c] == r[c] for c in pkc))
                    rec = conn.execute(sa.select(*(t.c[c] for c in bcols)).where(cond)).one()
                    for c in bcols:
                        raw = rec._mapping[c]
                        if raw is None:
                            blobs[(key, c)] = (None, 0)
                            continue
                        raw = bytes(raw)
                        path = f"blobs/{t.name}/{key}_{c}"
                        z.writestr(path, raw)
                        files[path] = sha(raw)
                        blobs[(key, c)] = (files[path], len(raw))
                        blob_count += 1
                        blob_bytes += len(raw)
            doc, _ = serialize_table(t, rows, columns, blobs, redact)
            path = f"data/{t.name}.json"
            z.writestr(path, doc)
            files[path] = sha(doc)
            manifest_tables[t.name] = {"file": path, "rows": len(rows), "sha256": files[path], "columns": columns}
            if t.name == "media":
                media_rows = rows
        # lista legible de archivos (multimedia)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "tenant_id", "key", "filename", "content_type", "size", "sha256", "created_at"])
        for r in media_rows:
            w.writerow([r.get("id"), r.get("tenant_id"), r.get("key"), r.get("filename"), r.get("content_type"), r.get("size"), r.get("sha256"), encode(r.get("created_at"))])
        idx = buf.getvalue().encode("utf-8")
        z.writestr("media_index.csv", idx)
        files["media_index.csv"] = sha(idx)
        manifest = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "created_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "alembic_revisions": alembic_revisions(conn),
            "source_dialect": dialect or conn.dialect.name,
            "scope": "tenant" if tid else "all",
            "tenant": {"id": tenant.id, "slug": tenant.slug, "name": tenant.name} if tenant is not None else None,
            "redacted": redact,
            "redacted_columns": sorted(f"{t}.{c}" for t, cs in REDACT_COLUMNS.items() for c in cs) if redact else [],
            "blobs_included": include_blobs,
            "skipped_tables": sorted(SKIP_TABLES - {"alembic_version"}),
            "tables": manifest_tables,
            "totals": {"tables": len(manifest_tables), "rows": sum(v["rows"] for v in manifest_tables.values()), "blobs": blob_count, "blob_bytes": blob_bytes},
            "files": files,
            "sha256": global_hash(files),
        }
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return manifest


def global_hash(files: dict[str, str]) -> str:
    return sha("".join(f"{p}\t{h}\n" for p, h in sorted(files.items())).encode())


# ---------- lectura / verificacion ----------
def read_manifest(z: zipfile.ZipFile) -> dict:
    try:
        m = json.loads(z.read("manifest.json"))
    except KeyError as e:
        raise BackupError("El archivo no es un respaldo de Crimson (falta manifest.json)") from e
    if m.get("format") != FORMAT:
        raise BackupError("El archivo no es un respaldo de Crimson")
    if int(m.get("format_version", 0)) > FORMAT_VERSION:
        raise BackupError(f"Respaldo en formato {m.get('format_version')}: actualice el sistema antes de restaurar")
    return m


def verify_zip(z: zipfile.ZipFile, m: dict) -> None:
    """Hash de cada archivo y hash global. Cualquier diferencia = respaldo corrupto o alterado."""
    for path, h in m["files"].items():
        try:
            got = sha(z.read(path))
        except KeyError as e:
            raise BackupError(f"Falta {path} dentro del respaldo") from e
        if got != h:
            raise BackupError(f"Hash distinto en {path}: el respaldo esta danado o fue modificado")
    if global_hash(m["files"]) != m["sha256"]:
        raise BackupError("El hash global del respaldo no coincide")


# ---------- restauracion ----------
def target_counts(conn: Connection, metadata: sa.MetaData) -> dict[str, int]:
    insp = sa.inspect(conn)
    out = {}
    for t in metadata.sorted_tables:
        if insp.has_table(t.name):
            out[t.name] = conn.execute(sa.select(sa.func.count()).select_from(t)).scalar_one()
    return out


def restore(conn: Connection, metadata: sa.MetaData, z: zipfile.ZipFile, m: dict, log=print) -> dict[str, int]:
    """Inserta todo en una base vacia (la transaccion la maneja quien llama). Devuelve filas por tabla."""
    tables, deferred = ordered_tables(metadata)
    known = {t.name for t in tables}
    extra = sorted(set(m["tables"]) - known)
    if extra:
        raise BackupError(f"El respaldo trae tablas que este sistema no conoce: {', '.join(extra)}. Restaure con la version del codigo del respaldo.")
    later: list[tuple[sa.Table, dict, dict]] = []
    counts: dict[str, int] = {}
    for t in tables:
        info = m["tables"].get(t.name)
        if not info:
            continue
        doc = json.loads(z.read(info["file"]))
        cols = doc["columns"]
        unknown = [c for c in cols if c not in t.c]
        if unknown:
            raise BackupError(f"{t.name}: columnas que no existen en el destino: {', '.join(unknown)}")
        missing = [c.name for c in t.columns if c.name not in cols and not c.nullable and c.default is None and c.server_default is None]
        if missing:
            raise BackupError(f"{t.name}: el destino exige columnas que el respaldo no trae: {', '.join(missing)}")
        dcols = deferred.get(t.name, set())
        upd_cols = [c.name for c in t.columns if c.onupdate is not None and c.name in cols]
        pkc = [c.name for c in _pk(t)]
        batch = []
        for vals in doc["rows"]:
            rec = {}
            for c, v in zip(cols, vals, strict=True):
                if isinstance(v, dict) and "$blob" in v:
                    rec[c] = z.read(v["$blob"]) if v["$blob"] else b""
                else:
                    rec[c] = decode(v, t.c[c])
            if dcols:
                pend = {c: rec[c] for c in dcols if rec.get(c) is not None}
                if pend:
                    for c in pend:
                        rec[c] = None
                    # las columnas con onupdate (updated_at) se reescriben con su valor original en el UPDATE
                    later.append((t, {c: rec[c] for c in pkc}, {**pend, **{c: rec[c] for c in upd_cols}}))
            batch.append(rec)
            if len(batch) >= 500:
                conn.execute(t.insert(), batch)
                batch = []
        if batch:
            conn.execute(t.insert(), batch)
        counts[t.name] = len(doc["rows"])
    for t, key, vals in later:
        conn.execute(t.update().where(sa.and_(*(t.c[k] == v for k, v in key.items()))).values(**vals))
    if conn.dialect.name == "postgresql":
        for t in tables:
            pk = list(t.primary_key.columns)
            if t.name in counts and len(pk) == 1 and isinstance(pk[0].type, sa.Integer):
                conn.execute(
                    sa.text(f"SELECT setval(pg_get_serial_sequence('\"{t.name}\"', '{pk[0].name}'), COALESCE(MAX(\"{pk[0].name}\"), 1), MAX(\"{pk[0].name}\") IS NOT NULL) FROM \"{t.name}\"")
                )
    log(f"Insertadas {sum(counts.values())} filas en {len(counts)} tablas ({len(later)} referencias completadas al final)")
    return counts


def verify_restored(conn: Connection, metadata: sa.MetaData, m: dict) -> list[str]:
    """Re-serializa el destino y compara conteos y hash de cada tabla con el manifest. Lista de problemas."""
    problems = []
    for name, info in sorted(m["tables"].items()):
        t = metadata.tables[name]
        rows = [dict(r) for r in _select_rows(conn, t, cols=[c for c in t.columns if c.name not in _blob_cols(t)])]
        if len(rows) != info["rows"]:
            problems.append(f"{name}: {len(rows)} filas en destino, {info['rows']} en el respaldo")
            continue
        bcols = _blob_cols(t)
        blobs = None
        if bcols and m.get("blobs_included", True):
            blobs = {}
            pkc = [c.name for c in _pk(t)]
            for r in rows:
                key = "-".join(str(r[c]) for c in pkc)
                rec = conn.execute(sa.select(*(t.c[c] for c in bcols)).where(sa.and_(*(t.c[c] == r[c] for c in pkc)))).one()
                for c in bcols:
                    raw = rec._mapping[c]
                    blobs[(key, c)] = (sha(bytes(raw)), len(bytes(raw))) if raw is not None else (None, 0)
        cols = info.get("columns") or [c.name for c in t.columns]
        got, _ = serialize_table(t, rows, cols, blobs, redact=False)
        if sha(got) != info["sha256"]:
            problems.append(f"{name}: el contenido restaurado no coincide con el respaldo (hash)")
    return problems


def write_to_path(conn: Connection, metadata: sa.MetaData, path: Path, **kw) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".parcial")
    try:
        m = write_backup(conn, metadata, tmp, **kw)
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return m
