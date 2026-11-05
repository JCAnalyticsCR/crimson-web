"""Un solo lugar que decide como se ve el estado de un comprobante recibido, con el vocabulario de Fygaro:

- accion(doc, rs, today): «Acción realizada» = Aceptada / Parcial / Rechazada (o Pendiente, Vencida, No aplica...).
- notificacion(doc, rs, today): «Notificación a Hacienda» = Aprobado / Rechazado / Pendiente...

Cada una devuelve {key, label, tone, hint, motivo, codigo, modo, vence, alerta}. La usan la lista, la ficha, el resumen,
Gastos, el Tributario, la exportacion y el correo diario; el portal solo pinta label y tone.

Regla de Pruebas (05/11): todo intento en mode=pruebas (el ensayo y tambien los -29 "clave no registrada" de antes) es
«Pendiente · solo en Pruebas» en ambar con la fecha limite, nunca un rechazo en rojo: el ambiente de Pruebas no conoce
las facturas reales de los proveedores y esa respuesta sigue pendiente ante la Hacienda real.

Funciones puras: no consultan la base (doc y rs ya cargados) ni la red.
"""

from __future__ import annotations

import re
from datetime import date

from .reception_calc import ACTION_OF_CODE, business_days_left

# «Condición del Impuesto (IVA)» con el texto de Hacienda (Fygaro muestra «Genera Crédito IVA», no «01 · ...»)
IVA_LABEL = {
    "credito": "Genera crédito IVA",
    "credito_parcial": "Genera crédito parcial del IVA",
    "bienes_capital": "Bienes de capital",
    "no_credito": "Gasto corriente no genera crédito",
    "proporcional": "Proporcionalidad",
}
# lo que Fygaro muestra en «Acción realizada»
ACCION = {"aceptada": ("Aceptada", "ok"), "parcial": ("Parcial", "warn"), "rechazada": ("Rechazada", "bad")}
EN_PROCESO = ("por_enviar", "enviando", "enviado", "procesando")
NO_APLICA = {
    "sin_respuesta": "No aplica: tiquete",
    "ajeno": "No aplica: no es para la empresa",
}
# los codigos de Hacienda vienen en DetalleMensaje como `-29, ""texto"", 0, 0` (mismo patron que HaciendaMensaje.tsx)
_CODE_RE = re.compile(r'(-?\d{1,4})\s*,\s*"+([\s\S]*?)"+\s*,\s*-?\d+\s*,\s*-?\d+')

# textos de hacienda_sync._fail/_apply_post/_apply_status que dicen POR QUE quedo en error un intento
YA_RECIBIDO = "Hacienda ya tiene un documento con esa clave"
AMBIGUOS = ("Hacienda no encuentra el documento", "Sin respuesta de Hacienda en 72 h")


def _out(key: str, label: str, tone: str, hint: str | None = None, **kw) -> dict:
    return {
        "key": key,
        "label": label,
        "tone": tone,
        "hint": hint,
        "motivo": kw.get("motivo"),
        "codigo": kw.get("codigo"),
        "modo": kw.get("modo"),
        "vence": kw.get("vence"),
        "alerta": kw.get("alerta"),
    }


def codes(detail: str | None) -> list[str]:
    """Codigos de Hacienda (-29, -37...) dentro de su DetalleMensaje."""
    raw = (detail or "").replace("&#13;", "\n")
    return [m.group(1) for m in _CODE_RE.finditer(raw)]


def motivo(detail: str | None) -> str | None:
    """El primer motivo de Hacienda en una linea (el texto entero si no trae la tabla de codigos)."""
    raw = (detail or "").replace("&#13;", "\n")
    m = _CODE_RE.search(raw)
    if m:
        return re.sub(r"\s+", " ", m.group(2)).strip()[:300] or None
    t = re.sub(r"\s+", " ", raw).strip()
    return t[:300] or None


def error_kind(r) -> str | None:
    """Por que quedo en error un intento (sin columna nueva: se lee de lo que dejo hacienda_sync).

    hacienda: Hacienda contesto (hay su XML): definitivo, como un rechazo; se puede responder de nuevo.
    ya_recibido: el primer POST y Hacienda YA tenia un mensaje para esa clave (¿Fygaro?): nunca se responde de nuevo.
    ambiguo: se envio y no se sabe si llego (no lo encuentra tras 10 min, o 72 h sin respuesta): hay que consultar.
    no_llego: no se envio, o Hacienda rechazo el envio (400 / validacion): no llego; se puede responder de nuevo.
    """
    if r is None or r.status != "error":
        return None
    if r.hacienda_xml:
        return "hacienda"
    err = r.last_error or ""
    if err.startswith(YA_RECIBIDO):
        return "ya_recibido"
    if (r.attempts or 0) > 0 and any(err.startswith(a) for a in AMBIGUOS):
        return "ambiguo"
    return "no_llego"


def _ddmm(d: date | None) -> str:
    return d.strftime("%d/%m") if d else "—"


def plazo_txt(left: int | None) -> str | None:
    if left is None:
        return None
    if left < 0:
        return "Plazo vencido"
    if left == 0:
        return "Vence hoy"
    return f"Queda{'n' if left != 1 else ''} {left} día{'s' if left != 1 else ''} hábil{'es' if left != 1 else ''}"


def _overdue(doc, today: date) -> bool:
    return bool(doc.deadline and doc.deadline < today)


def _alerta_proveedor(doc) -> str | None:
    if doc.supplier_hacienda_status == "rechazado" and doc.status in ("respondido", "historico"):
        return "Hacienda rechazó esta factura al proveedor: no da crédito; pídale una nueva"
    return None


def _decision(doc, rs) -> str | None:
    """La accion que vale: la del documento; si no la tiene, la del ultimo intento."""
    if doc.action:
        return doc.action
    last = rs[-1] if rs else None
    return ACTION_OF_CODE.get(last.message_code) if last is not None else None


def accion(doc, rs=None, today: date | None = None) -> dict:
    rs = list(rs or [])
    today = today or date.today()
    vence = doc.deadline.isoformat() if doc.deadline else None
    st = doc.status
    if st == "descartado":
        return _out("descartado", "Descartado", "muted", doc.discarded_reason)
    if st in NO_APLICA:
        label = NO_APLICA[st] if (st != "sin_respuesta" or doc.doc_type == "TE") else "No aplica: no lleva respuesta"
        return _out("no_aplica", label, "muted")
    if st == "invalido":
        return _out("invalido", "No se responde: Hacienda rechazó la factura al proveedor", "bad", doc.supplier_hacienda_detail and motivo(doc.supplier_hacienda_detail))
    if st == "por_revisar":
        left = business_days_left(today, doc.deadline)
        if _overdue(doc, today):
            return _out("vencida", "Vencida sin responder", "bad", "Hacienda presume la aceptación total", vence=vence)
        hint = plazo_txt(left)
        tone = "bad" if left is not None and left <= 2 else "warn"
        if rs:
            return _out("pendiente", "Pendiente · se responde de nuevo", tone, hint, vence=vence)
        return _out("pendiente", "Pendiente", tone, hint, vence=vence)
    act = _decision(doc, rs)
    label, tone = ACCION.get(act or "", ("Respondida", "info"))
    hint = "Respondida en Fygaro" if st == "historico" or (rs and rs[-1].status == "historico") else None
    return _out(act or "respondida", label, tone, hint, vence=vence, alerta=_alerta_proveedor(doc))


def notificacion(doc, rs=None, today: date | None = None) -> dict:
    rs = list(rs or [])
    today = today or date.today()
    vence = doc.deadline.isoformat() if doc.deadline else None
    last = rs[-1] if rs else None
    st = doc.status
    if st in ("sin_respuesta", "ajeno", "invalido") or (st == "descartado" and not rs):
        return _out("no_aplica", "No aplica", "muted")
    if st == "historico" or (last is not None and last.status == "historico"):
        return _out("fygaro", "Respondido en Fygaro", "ok", modo="historico")
    if st == "por_revisar":
        # el plazo ya lo dice «Acción realizada»: aqui no se repite
        return _out("pendiente", "Pendiente", "muted", "Se responde de nuevo" if rs else None, vence=vence)
    if last is None:
        if doc.hacienda_status == "simulado":  # respondido antes de la migracion (legacy_simulated)
            return _out("simulado", "Simulado · no se envió", "muted", "Sin validez fiscal", modo="simulado")
        return _out("pendiente", "Pendiente", "muted", vence=vence)
    det = last.hacienda_detail or last.last_error
    cods = codes(last.hacienda_detail)
    # el motivo solo de la Hacienda REAL: lo que contesta Pruebas (el -29) no se muestra como si fuera un rechazo
    real = last.mode == "produccion" and last.status in ("rechazado", "error")
    kw = {"motivo": motivo(det) if real else None, "codigo": cods[0] if cods else None, "modo": last.mode, "vence": vence}
    if last.mode == "simulado" or last.status == "simulado":
        return _out("simulado", "Simulado · no se envió", "muted", "Sin validez fiscal", **kw)
    if last.mode == "pruebas":
        if _overdue(doc, today):
            return _out(
                "pruebas_vencida",
                "No enviada · plazo vencido",
                "bad",
                "Solo se respondió en Pruebas. Si la respondió en Fygaro, importe ese mensaje (Importar histórico) para que conste aquí",
                **kw,
            )
        return _out("solo_pruebas", "Pendiente · solo en Pruebas", "warn", f"Falta enviarla en Producción antes del {_ddmm(doc.deadline)}", **kw)
    # produccion
    if last.status == "aceptado":
        return _out("aprobado", "Aprobado", "ok", **kw)
    if last.status == "rechazado":
        return _out("rechazado", "Rechazado", "bad", "Revise el motivo y responda de nuevo", **kw)
    if last.status in EN_PROCESO:
        return _out("en_proceso", "Pendiente · en proceso", "info", "Hacienda todavía no contesta" if last.status != "por_enviar" else "Por enviar", **kw)
    if last.status == "error":
        k = error_kind(last)
        if k == "hacienda":
            return _out("rechazado", "Rechazado", "bad", "Revise el motivo y responda de nuevo", **kw)
        if k == "ya_recibido":
            return _out("ya_recibido", "Ya respondido en otro sistema", "bad", "Verifique en Fygaro e importe ese mensaje (Importar histórico)", **kw)
        if k == "ambiguo":
            return _out("verificar", "Error · verificar", "bad", "No se sabe si llegó: consulte a Hacienda antes de responder de nuevo", **kw)
        return _out("no_llego", "No llegó · responda de nuevo", "warn", None, **kw)
    return _out("pendiente", "Pendiente", "muted", **kw)


def pending_production(doc, rs) -> bool:
    """Respondido y su ultimo intento es de Pruebas: ante la Hacienda real sigue pendiente."""
    last = rs[-1] if rs else None
    return doc.status == "respondido" and last is not None and last.mode == "pruebas"
