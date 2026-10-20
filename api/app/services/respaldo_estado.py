"""Estado del respaldo diario (plan 1.9) en la tabla `task_run` (corridas de tareas, plan 1.8).

- `registrar()`: lo llama `app.tools.respaldo_diario` al terminar, bien o mal. Deja una fila `task="respaldo.diario"`
  con inicio, fin, ok, el resumen (archivo, hashes, tamanos, filas y verificacion: sin datos personales) y el error.
  Como es una corrida mas de task_run, el correo diario de Salud ya la lista si fallo.
- `revision()`: lo que el correo de Salud y `/health/deep` muestran del respaldo (el ultimo, hace cuantas horas, hash,
  tamano y filas), con la forma de las demas revisiones de Salud (`ok`, `critico` y el detalle). Es un AVISO, nunca
  critico: si el ultimo fallo o tiene mas de 26 h (el cron corre cada 24 h; 2 h de gracia, igual que el latido).
  Para mostrarlo en Salud basta una linea en la lista de revisiones: `"respaldo": respaldo_estado.revision(db, now)`.

Las dos toleran que task_run todavia no exista (el plan 1.8 se fusiona aparte) y leen la tabla con Core, sin depender
del modelo. `registrar()` nunca rompe el respaldo: el latido y el correo de falla no dependen de la base.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

TAREA = "respaldo.diario"
MAX_HORAS = 26
ERROR_MAX = 500
# columnas de task_run (plan 1.8) que usa el respaldo; se insertan solo las que la tabla tenga
_COLUMNAS = {
    "task": sa.String(80),
    "task_id": sa.String(64),
    "started_at": sa.DateTime(timezone=True),
    "finished_at": sa.DateTime(timezone=True),
    "ok": sa.Boolean(),
    "summary": sa.JSON(),
    "error": sa.Text(),
}
_MINIMAS = {"task", "started_at", "ok"}


def _tabla(conn: sa.Connection) -> sa.Table | None:
    insp = sa.inspect(conn)
    if not insp.has_table("task_run"):
        return None
    nombres = {c["name"] for c in insp.get_columns("task_run")}
    if not _MINIMAS <= nombres:
        return None
    cols = [sa.Column(n, t) for n, t in _COLUMNAS.items() if n in nombres]
    return sa.Table("task_run", sa.MetaData(), sa.Column("id", sa.Integer, primary_key=True), *cols)


def _utc(d: datetime) -> datetime:
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


def _engine(url: str) -> sa.Engine:
    # conexion corta y con tope: si la base es lo que fallo, el registro no puede dejar colgado el cron
    connect_args = {"connect_timeout": 10} if url.startswith("postgresql") else {}
    return sa.create_engine(url, pool_pre_ping=True, connect_args=connect_args)


def registrar(
    url: str,
    *,
    corrida: str,
    inicio: datetime,
    fin: datetime,
    ok: bool,
    resumen: dict | None,
    error: str | None,
) -> bool:
    """Una fila en task_run con la corrida del respaldo. Devuelve si quedo registrada; nunca lanza."""
    try:
        eng = _engine(url)
        try:
            with eng.begin() as conn:
                t = _tabla(conn)
                if t is None:
                    print("AVISO: la base no tiene task_run (plan 1.8): la corrida del respaldo no queda registrada", file=sys.stderr)
                    return False
                fila = {
                    "task": TAREA,
                    "task_id": corrida[:64],
                    "started_at": _utc(inicio),
                    "finished_at": _utc(fin),
                    "ok": ok,
                    "summary": resumen,
                    "error": error[:ERROR_MAX] if error else None,
                }
                conn.execute(t.insert().values({k: v for k, v in fila.items() if k in t.c}))
        finally:
            eng.dispose()
        return True
    except Exception as e:  # noqa: BLE001  (registrar es lo de menos: el respaldo ya se subio o ya aviso)
        print(f"AVISO: no se pudo registrar la corrida del respaldo en task_run ({type(e).__name__})", file=sys.stderr)
        return False


def _tamano(n) -> str:
    try:
        return f"{int(n) / 1_048_576:.1f} MB"
    except (TypeError, ValueError):
        return "—"


def revision(bind: Session | sa.Connection, now: datetime | None = None, max_horas: float = MAX_HORAS) -> dict | None:
    """Ultimo respaldo segun task_run, para el correo de Salud y /health/deep. None si la tabla no existe todavia."""
    now = _utc(now or datetime.now(UTC))
    conn = bind.connection() if isinstance(bind, Session) else bind
    t = _tabla(conn)
    if t is None:
        return None
    orden = t.c.started_at.desc()
    ultima = conn.execute(sa.select(t).where(t.c.task == TAREA).order_by(orden).limit(1)).mappings().first()
    if ultima is None:
        return {"ok": False, "critico": False, "ultimo": "no hay respaldos registrados"}
    buena = ultima if ultima["ok"] else conn.execute(sa.select(t).where(t.c.task == TAREA, t.c.ok.is_(True)).order_by(orden).limit(1)).mappings().first()
    out: dict = {"ok": bool(ultima["ok"]), "critico": False}
    if buena is None:
        out["ultimo"] = "ninguno salió bien"
    else:
        inicio = _utc(buena["started_at"])
        horas = (now - inicio).total_seconds() / 3600
        r = buena.get("summary") or {}  # sin la columna, .get da None
        out["ultimo"] = f"{inicio.strftime('%Y-%m-%d %H:%M')} UTC (hace {horas:.0f} h)"
        out["filas"] = r.get("filas", "—")
        out["tamano"] = _tamano(r.get("age_bytes"))
        out["sha256"] = f"{str(r['zip_sha256'])[:16]}…" if r.get("zip_sha256") else "—"
        out["verificacion"] = r.get("verificacion", "—")
        if horas > max_horas:
            out["ok"] = False
            out["vencido"] = f"más de {max_horas:.0f} h sin un respaldo bueno"
    if not ultima["ok"]:
        out["fallo"] = str(ultima.get("error") or "sin detalle")[:200]
    return out
