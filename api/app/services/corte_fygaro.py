"""Corte de Fygaro: la empresa uso Fygaro hasta el 31/10/2026 y Crimson desde el 1/11/2026, con Hacienda en Produccion.

Un aviso que manda a "confirmar que consta en Fygaro" solo tiene sentido para un documento de antes del corte (fecha
del comprobante): lo de despues nunca paso por Fygaro y ya no hay donde responderlo alla. Lo historico legitimo
(estados "Historico (Fygaro)", "Emitida en Fygaro", el importador) no depende de esto.
"""

from __future__ import annotations

from datetime import date

CORTE = date(2026, 11, 1)  # primer dia en Crimson


def antes_del_corte(d: date | None) -> bool:
    return d is not None and d < CORTE
