"""Logging de api y worker (plan 1.8): INFO y DEBUG a stdout, WARNING o mas a stderr, cada linea con nivel y logger.

Railway marca como error todo lo que sale por stderr. Antes los INFO de la app no salian (sin handler) y los de
uvicorn y Celery iban a stderr: el panel se veia todo en rojo y un error de verdad no se distinguia.

- api: create_app() lo aplica despues de que uvicorn configuro sus loggers; los de uvicorn pasan a usar estos handlers.
- worker: se conecta a la señal setup_logging de Celery, que entonces no toca el logging (ni redirige stdout).

Los handlers buscan sys.stdout / sys.stderr al escribir (no al crearse): asi funcionan con la captura de pytest.
"""

from __future__ import annotations

import logging
import os
import sys

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_MARK = "_crimson_handler"


class _Stream(logging.StreamHandler):
    """StreamHandler hacia sys.<nombre> resuelto en cada escritura."""

    def __init__(self, name: str):
        self._stream_name = name
        super().__init__()

    @property
    def stream(self):  # type: ignore[override]
        return getattr(sys, self._stream_name)

    @stream.setter
    def stream(self, _value) -> None:  # StreamHandler.__init__ lo asigna; se ignora
        pass


def _below_warning(record: logging.LogRecord) -> bool:
    return record.levelno < logging.WARNING


def handlers() -> list[logging.Handler]:
    fmt = logging.Formatter(FORMAT)
    out = _Stream("stdout")
    out.setLevel(logging.DEBUG)
    out.addFilter(_below_warning)
    err = _Stream("stderr")
    err.setLevel(logging.WARNING)
    for h in (out, err):
        h.setFormatter(fmt)
        setattr(h, _MARK, True)
    return [out, err]


def setup_logging(level: int | str | None = None) -> None:
    """Idempotente: reemplaza solo los handlers propios del root (los de pytest u otros quedan) y hace que uvicorn
    escriba por estos. Nivel: el argumento, o LOG_LEVEL, o INFO."""
    level = level or os.getenv("LOG_LEVEL") or logging.INFO
    if isinstance(level, str):
        level = logging.getLevelName(level.strip().upper())
        if not isinstance(level, int):
            level = logging.INFO
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not getattr(h, _MARK, False)] + handlers()
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
