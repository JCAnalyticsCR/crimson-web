"""Estado del respaldo diario (plan 1.9) en la tabla `task_run` (corridas de tareas, plan 1.8).

- `registrar()`: lo llama `app.tools.respaldo_diario` al terminar, bien o mal. Deja una fila `tarea="respaldo.diario"`
  con inicio, fin, ok, el resumen (archivo, hashes, tamanos, filas y verificacion: sin datos personales) y el error.
- `revision()`: la linea del respaldo en el correo de **Salud** (interno): el ultimo bueno, hace cuantas horas, hash,
  tamano, filas y el error de la ultima corrida si fallo. Con la forma de las demas revisiones de Salud (`ok`, `critico`
  y el detalle). Es un AVISO, nunca critico: si el ultimo fallo o tiene mas de 26 h (el cron corre cada 24 h; 2 h de
  gracia, igual que el latido).
- `revision_publica()`: lo unico que puede ir en `/health/deep` (lo consulta un monitor externo): `ok`, `critico`,
  `vencido` y las horas desde el ultimo respaldo bueno. Nada del error (trae hosts, usuarios o el bucket), ni filas,
  tamano o hash.

PENDIENTE AL INTEGRAR EL PLAN 1.8 (este worktree no tiene Salud ni /health/deep):
  - en las revisiones del correo de Salud (`app/tasks/salud.py`): `"respaldo": respaldo_estado.revision(db, now)`;
  - en `/health/deep`: `"respaldo": respaldo_estado.revision_publica(db, now)` (nunca `revision()`);
  - una prueba en `test_salud.py` que verifique que la linea aparece, y que corra
    `tests/test_infra_respaldo_diario.py::test_task_run_del_modelo_recibe_la_corrida` (se activa sola cuando el modelo
    TaskRun queda en Base.metadata: si los nombres de columnas no calzan, falla ahi y no en produccion).

Las columnas se leen de la tabla real (reflexion), sin depender del modelo: se aceptan los nombres en ingles o en espanol
(`task`/`tarea`, `started_at`/`inicio`, `finished_at`/`fin`, `summary`/`resumen`, ...) y se llenan `created_at` y
`updated_at` si la tabla los tiene. Si falta una columna obligatoria que el respaldo no sabe llenar, se avisa en el log
nombrandola. Toleran que task_run todavia no exista. `registrar()` nunca rompe el respaldo: el latido y el correo de
falla no dependen de la base.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

TAREA = "respaldo.diario"
MAX_HORAS = 26
ERROR_MAX = 500
# campo logico -> nombres aceptados en task_run (el primero que exista). El plan 1.8 la describe como
# "tarea, inicio, fin, ok, resumen, error"; el repo nombra las columnas en ingles.
_ALIAS = {
    "tarea": ("task", "tarea", "task_name", "name"),
    "corrida": ("task_id", "corrida", "run_id"),
    "inicio": ("started_at", "inicio", "start_at"),
    "fin": ("finished_at", "fin", "ended_at"),
    "ok": ("ok", "success", "exito"),
    "resumen": ("summary", "resumen", "result"),
    "error": ("error", "error_message"),
}
_MINIMOS = ("tarea", "inicio", "ok")
_SELLOS = ("created_at", "updated_at")


class _TaskRun:
    """task_run tal como esta en la base, con sus columnas resueltas a los campos que usa el respaldo."""

    def __init__(self, tabla: sa.Table):
        self.tabla = tabla
        self.col: dict[str, sa.Column] = {}
        for campo, nombres in _ALIAS.items():
            for n in nombres:
                if n in tabla.c:
                    self.col[campo] = tabla.c[n]
                    break

    @property
    def completa(self) -> bool:
        return all(c in self.col for c in _MINIMOS)

    def fila(self, valores: dict, ahora: datetime) -> dict:
        out = {}
        for campo, v in valores.items():
            c = self.col.get(campo)
            if c is None:
                continue
            if isinstance(v, dict | list) and not isinstance(c.type, sa.JSON):
                v = json.dumps(v, ensure_ascii=False, sort_keys=True)  # una columna de texto: el resumen va como JSON
            if isinstance(v, str) and getattr(c.type, "length", None):
                v = v[: c.type.length]
            out[c.name] = v
        for n in _SELLOS:  # TimestampMixin: NOT NULL con default solo en Python, que un insert Core no aplica
            if n in self.tabla.c and n not in out:
                out[n] = ahora
        return out

    def faltantes(self, fila: dict) -> list[str]:
        """Columnas obligatorias sin valor ni default en la base (p. ej. un tenant_id NOT NULL)."""
        return [
            c.name
            for c in self.tabla.columns
            if c.name not in fila and not c.nullable and c.server_default is None and not (c.primary_key and isinstance(c.type, sa.Integer))
        ]

    def resumen_de(self, r) -> dict:
        c = self.col.get("resumen")
        v = r.get(c.name) if c is not None else None
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except ValueError:
                v = None
        return v if isinstance(v, dict) else {}


def _tabla(conn: sa.Connection) -> _TaskRun | None:
    if not sa.inspect(conn).has_table("task_run"):
        return None
    t = _TaskRun(sa.Table("task_run", sa.MetaData(), autoload_with=conn))
    return t if t.completa else None


def _utc(d: datetime) -> datetime:
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


def _engine(url: str) -> sa.Engine:
    # conexion corta y con tope: si la base es lo que fallo, el registro no puede dejar colgado el cron
    connect_args = {"connect_timeout": 10} if url.startswith("postgresql") else {}
    return sa.create_engine(url, pool_pre_ping=True, connect_args=connect_args, hide_parameters=True)


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
                    print("AVISO: la base no tiene task_run (plan 1.8) con tarea, inicio y ok: la corrida del respaldo no queda registrada", file=sys.stderr)
                    return False
                valores = {
                    "tarea": TAREA,
                    "corrida": corrida,
                    "inicio": _utc(inicio),
                    "fin": _utc(fin),
                    "ok": ok,
                    "resumen": resumen,
                    "error": error[:ERROR_MAX] if error else None,
                }
                fila = t.fila(valores, datetime.now(UTC))
                faltan = t.faltantes(fila)
                if faltan:
                    print(
                        f"AVISO: task_run exige columnas que el respaldo no sabe llenar ({', '.join(faltan)}): la corrida no queda registrada", file=sys.stderr
                    )
                    return False
                conn.execute(t.tabla.insert().values(fila))
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


def _ultimas(conn: sa.Connection, t: _TaskRun):
    """(ultima corrida, ultima buena) del respaldo, o None si task_run no existe todavia."""
    tarea, inicio, ok = t.col["tarea"], t.col["inicio"], t.col["ok"]
    base = sa.select(t.tabla).where(tarea == TAREA).order_by(inicio.desc()).limit(1)
    ultima = conn.execute(base).mappings().first()
    if ultima is None or ultima[ok.name]:
        return ultima, ultima
    return ultima, conn.execute(base.where(ok.is_(True))).mappings().first()


def _conn(bind: Session | sa.Connection) -> sa.Connection:
    return bind.connection() if isinstance(bind, Session) else bind


def _vencido(horas: float | None, max_horas: float) -> bool:
    return horas is None or horas > max_horas


def revision_publica(bind: Session | sa.Connection, now: datetime | None = None, max_horas: float = MAX_HORAS) -> dict | None:
    """Para /health/deep (sin datos sensibles): ok, critico, vencido y horas desde el ultimo respaldo bueno.
    None si la tabla no existe todavia."""
    now = _utc(now or datetime.now(UTC))
    conn = _conn(bind)
    t = _tabla(conn)
    if t is None:
        return None
    ultima, buena = _ultimas(conn, t)
    horas = round((now - _utc(buena[t.col["inicio"].name])).total_seconds() / 3600, 1) if buena is not None else None
    vencido = _vencido(horas, max_horas)
    ok = ultima is not None and bool(ultima[t.col["ok"].name]) and not vencido
    return {"ok": ok, "critico": False, "vencido": vencido, "horas_desde_el_ultimo_bueno": horas}


def revision(bind: Session | sa.Connection, now: datetime | None = None, max_horas: float = MAX_HORAS) -> dict | None:
    """Para el correo de Salud (interno): el ultimo respaldo bueno con su detalle y el error de la ultima corrida si fallo.
    No va en /health/deep (ver revision_publica). None si la tabla no existe todavia."""
    now = _utc(now or datetime.now(UTC))
    conn = _conn(bind)
    t = _tabla(conn)
    if t is None:
        return None
    ultima, buena = _ultimas(conn, t)
    if ultima is None:
        return {"ok": False, "critico": False, "ultimo": "no hay respaldos registrados"}
    out: dict = {"ok": bool(ultima[t.col["ok"].name]), "critico": False}
    if buena is None:
        out["ultimo"] = "ninguno salió bien"
    else:
        inicio = _utc(buena[t.col["inicio"].name])
        horas = (now - inicio).total_seconds() / 3600
        r = t.resumen_de(buena)
        out["ultimo"] = f"{inicio.strftime('%Y-%m-%d %H:%M')} UTC (hace {horas:.0f} h)"
        out["filas"] = r.get("filas", "—")
        out["tamano"] = _tamano(r.get("age_bytes"))
        out["sha256"] = f"{str(r['zip_sha256'])[:16]}…" if r.get("zip_sha256") else "—"
        out["verificacion"] = r.get("verificacion", "—")
        if _vencido(horas, max_horas):
            out["ok"] = False
            out["vencido"] = f"más de {max_horas:.0f} h sin un respaldo bueno"
    if not ultima[t.col["ok"].name]:
        c = t.col.get("error")
        out["fallo"] = str((ultima[c.name] if c is not None else None) or "sin detalle")[:200]
    return out
