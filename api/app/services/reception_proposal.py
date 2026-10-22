"""Respuesta sugerida a cada comprobante recibido (reunion 2, pedido 4: "Fygaro lo hacia por detras").

Por cada factura de gasto que llega (buzon o carga manual) la plataforma deja preparada la respuesta a Hacienda para
que el admin la apruebe con un clic: aceptacion (total), condicion del IVA, actividad, categoria del gasto y el gasto
a vincular. "Aprobar y enviar" (POST /reception/{id}/approve) la manda por el MISMO flujo que "Responder"
(services/reception.respond: consecutivo, mensaje receptor firmado, envio por hacienda_sync, gasto ligado). Nada sale
solo: sin la aprobacion de una persona no se responde a Hacienda.

De donde sale la sugerencia:
1. Nota de credito que corrige una factura rechazada o aceptada a medias: aceptar sin credito y sin gasto (ya quedo
   por el monto aceptado).
2. El ultimo comprobante del MISMO proveedor que se acepto: su condicion del IVA, su actividad y la categoria de su
   gasto (lo que contabilidad decidio la vez anterior).
3. Si es el primero de ese proveedor: aceptacion total con "01 genera credito" (o la condicion por defecto de la
   empresa, settings["reception_default_iva_condition"]), la actividad por defecto y sin categoria.

No queda lista (ready=False, se responde a mano desde el detalle) cuando hace falta una decision: credito parcial (hay
que decir cuanto), un gasto ya registrado que parece ser este (vincularlo o no), sin actividad por defecto, sin la
cedula de la empresa, con produccion bloqueada en el servidor o cuando Hacienda le rechazo el comprobante al
proveedor (no da credito: aceptarlo, rechazarlo o pedir uno nuevo lo decide una persona). Los rechazos y las
aceptaciones parciales nunca se sugieren: son decisiones de una persona.

Huella (fingerprint): resume lo que se va a enviar. El portal la manda al aprobar; si la sugerencia cambio entre la
vista y el clic (otro comprobante del proveedor aceptado, un gasto nuevo que se vincularia) no se envia nada (409).

Costo: propose() hace varias consultas por comprobante. `Ctx` las comparte dentro de una peticion (proveedores de la
empresa, actividad por defecto, bloqueos globales) y los contadores de "listas para aprobar" se topan (COUNT_CAP).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, defer

from ..models import Expense, ExpenseCategory, ReceivedDocument, Supplier, Tenant
from ..providers.hacienda import xml44
from . import hacienda_config as hc

CONDITIONS = ("credito", "credito_parcial", "bienes_capital", "no_credito", "proporcional")
COUNT_CAP = 50  # "listas para aprobar" en los contadores: mas alla de esto la bandeja las muestra igual
STALE_MINUTES = 60  # el worker revisa el buzon cada 15 minutos: sin una corrida buena en 1 hora, algo falla
_CR = timezone(timedelta(hours=-6))  # Costa Rica no tiene horario de verano
_UNSET = object()


class Ctx:
    """Lo que comparten las sugerencias de una misma peticion (una lista de 50 no consulta 50 veces lo mismo)."""

    def __init__(self, db: Session, tenant: Tenant):
        self.db, self.tenant = db, tenant
        self._sups: list[tuple[int, str | None]] | None = None
        self._default_act = _UNSET
        self._global: list[str] | None = None

    def suppliers_of(self, issuer_id: str) -> list[int]:
        if self._sups is None:
            self._sups = [(i, t) for i, t in self.db.execute(select(Supplier.id, Supplier.tax_id).where(Supplier.tenant_id == self.tenant.id)).all()]
        return [i for i, t in self._sups if xml44.same_id(t, issuer_id)]

    def default_activity(self):
        if self._default_act is _UNSET:
            self._default_act = hc.default_activity(self.db, self.tenant.id)
        return self._default_act

    def global_blockers(self) -> list[str]:
        if self._global is None:
            self._global = _global_blockers(self.db, self.tenant)
        return list(self._global)


def _previous(db: Session, doc: ReceivedDocument) -> ReceivedDocument | None:
    """El ultimo comprobante aceptado del mismo proveedor (sin contar este)."""
    if not doc.issuer_id:
        return None
    q = (
        select(ReceivedDocument)
        .where(
            ReceivedDocument.tenant_id == doc.tenant_id,
            ReceivedDocument.id != doc.id,
            ReceivedDocument.issuer_id == doc.issuer_id,
            ReceivedDocument.action.in_(("aceptada", "parcial")),
            ReceivedDocument.status.in_(("respondido", "historico")),
            ReceivedDocument.doc_type != "NC",
        )
        .order_by(ReceivedDocument.issue_date.desc().nulls_last(), ReceivedDocument.id.desc())
        .limit(1)
        # los XML completos no hacen falta (con el historico de Fygaro son miles de filas pesadas)
        .options(defer(ReceivedDocument.xml_document), defer(ReceivedDocument.xml_supplier_response), defer(ReceivedDocument.xml_response))
    )
    return db.scalar(q)


def _category_of_supplier(db: Session, doc: ReceivedDocument, prev: ReceivedDocument | None, ctx: Ctx) -> ExpenseCategory | None:
    cid = None
    if prev is not None and prev.expense_id:
        e = db.get(Expense, prev.expense_id)
        cid = e.category_id if e is not None and e.tenant_id == doc.tenant_id else None
    if cid is None and doc.issuer_id:
        sups = ctx.suppliers_of(doc.issuer_id)
        if sups:
            cid = db.scalar(
                select(Expense.category_id)
                .where(Expense.tenant_id == doc.tenant_id, Expense.supplier_id.in_(sups), Expense.category_id.is_not(None), Expense.status != "anulado")
                .order_by(Expense.date.desc(), Expense.id.desc())
                .limit(1)
            )
    cat = db.get(ExpenseCategory, cid) if cid else None
    return cat if cat is not None and cat.tenant_id == doc.tenant_id and cat.active else None


def fingerprint(prop: dict) -> str:
    """Huella de lo que se enviaria (accion, condicion, actividad, detalle, gasto y categoria)."""
    keys = ("action", "iva_condition", "activity_code", "detail", "create_expense", "link_expense_id", "category_id")
    raw = json.dumps([prop.get(k) for k in keys], default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _done(prop: dict) -> dict:
    prop["ready"] = not prop["blockers"]
    prop["fingerprint"] = fingerprint(prop)
    return prop


def propose(db: Session, tenant: Tenant, doc: ReceivedDocument, ctx: Ctx | None = None) -> dict | None:
    """La respuesta sugerida, o None si este comprobante no se responde (ya respondido, tiquete, ajeno...)."""
    from . import reception as rsvc

    if doc.status != "por_revisar" or doc.doc_type == "TE" or not rsvc.can_respond(db, doc):
        return None
    ctx = ctx or Ctx(db, tenant)
    reasons: list[str] = []
    blockers: list[str] = []
    if doc.supplier_hacienda_status == "rechazado":
        # Hacienda se lo rechazo al proveedor: sin validez fiscal ni credito. Nunca se acepta "con un clic".
        blockers.append("Hacienda rechazó este comprobante al proveedor (no da crédito): revíselo y decida si rechazarlo o pedir uno nuevo")
    nc_ref = rsvc.nc_adjustment(db, doc)
    if nc_ref is not None:
        # sin credito (04) el mensaje lleva actividad: la por defecto, o no queda lista (respond daria 422)
        act = ctx.default_activity()
        if act is None:
            blockers.append("Configure una actividad económica por defecto en Ajustes → Hacienda")
        return _done(
            {
                "action": "aceptada",
                "iva_condition": "no_credito",
                "activity_code": act.code if act is not None else None,
                "create_expense": False,
                "link_expense_id": None,
                "category_id": None,
                "category": None,
                "detail": None,
                "reference_id": nc_ref.id,
                "source": "nota_credito",
                "reasons": ["Nota de crédito de una factura que se rechazó o aceptó a medias: se acepta sin crédito ni gasto"],
                "blockers": blockers + ctx.global_blockers(),
            }
        )
    prev = _previous(db, doc)
    default_cond = (tenant.settings or {}).get("reception_default_iva_condition") or "credito"
    cond = prev.iva_condition if prev is not None and prev.iva_condition in CONDITIONS else (default_cond if default_cond in CONDITIONS else "credito")
    if prev is not None:
        reasons.append(f"Como el último comprobante aceptado de este proveedor ({prev.consecutive or prev.clave[-10:]})")
    else:
        reasons.append("Primer comprobante de este proveedor: aceptación total con la condición por defecto")
    if cond == "credito_parcial":
        blockers.append("La vez anterior se acreditó solo parte del IVA: indique cuánto en el detalle")
    activity = None
    if cond != "proporcional":
        act = None
        if prev is not None and prev.activity_code:
            act = hc.active_activity(db, tenant.id, prev.activity_code)
        act = act or ctx.default_activity()
        if act is None:
            blockers.append("Configure una actividad económica por defecto en Ajustes → Hacienda")
        else:
            activity = act.code
    cat = _category_of_supplier(db, doc, prev, ctx)
    link_id = None
    cands = rsvc.expense_candidates(db, doc)
    if cands:
        best = cands[0]
        if best["supplier"] and best["diff"] == 0:
            link_id = best["id"]
            reasons.append(f"Se vincula al gasto ya registrado del {best['date'].strftime('%d/%m/%Y')} (mismo proveedor y monto)")
        else:
            blockers.append("Hay un gasto registrado que parece ser este comprobante: ábralo para vincularlo o registrar uno nuevo")
    if rsvc.overdue(doc):
        reasons.append("Fuera de plazo: Hacienda presume la aceptación total y puede rechazar el mensaje")
    blockers += ctx.global_blockers()
    return _done(
        {
            "action": "aceptada",
            "iva_condition": cond,
            "activity_code": activity,
            "create_expense": link_id is None,
            "link_expense_id": link_id,
            "category_id": cat.id if cat is not None and link_id is None else None,
            "category": cat.name if cat is not None and link_id is None else None,
            "detail": None,
            "reference_id": None,
            "source": "proveedor" if prev is not None else "predeterminado",
            "reasons": reasons,
            "blockers": blockers,
        }
    )


def ready_count(db: Session, tenant: Tenant, docs: list[ReceivedDocument]) -> int:
    """Cuantas tienen la respuesta lista (topado en COUNT_CAP: es un contador, la bandeja muestra todas)."""
    ctx = Ctx(db, tenant)
    return sum(1 for d in docs[:COUNT_CAP] if (propose(db, tenant, d, ctx) or {}).get("ready"))


def _global_blockers(db: Session, tenant: Tenant) -> list[str]:
    out = []
    if not tenant.tax_id:
        out.append("Configure la cédula de la empresa en Ajustes → Hacienda")
    blocked = hc.production_blocked(db, tenant)
    if blocked:
        out.append(f"Producción está activada pero este servidor no la permite ({blocked})")
    return out


def respond_in(prop: dict):
    """El cuerpo de "Responder" que equivale a la sugerencia (el mismo validador de siempre)."""
    from .reception import RespondIn

    return RespondIn(
        action=prop["action"],
        iva_condition=prop["iva_condition"],
        activity_code=prop["activity_code"],
        detail=prop["detail"],
        create_expense=prop["create_expense"],
        link_expense_id=prop["link_expense_id"],
        category_id=prop["category_id"],
        send_now=True,
    )


def inbox_state(db: Session, tenant: Tenant) -> dict:
    """Estado del buzon en palabras (en vez de "sin revisar"): apagado, nunca revisado, revisado con o sin errores."""
    from .inbox import status

    st = status(db, tenant)
    last = st.get("last_result") or {}
    errores = [e for e in (last.get("errores") or []) if e] if isinstance(last, dict) else []
    if not st["enabled"]:
        return {
            **st,
            "state": "apagado",
            "label": "Apagado: configure el correo de recepción en Ajustes → XML de proveedores (mientras tanto, cargue los XML a mano)",
        }
    if not st["last_run_at"]:
        return {
            **st,
            "state": "pendiente",
            "label": "Configurado: todavía no se ha revisado (se revisa solo cada 15 minutos). Si sigue así, use «Revisar buzón ahora» para ver el error",
        }
    # una corrida que falla (contraseña rotada, servidor caido) no guarda nada: la ultima buena se queda vieja
    when = _aware(st["last_run_at"])
    if when is not None and datetime.now(UTC) - when > timedelta(minutes=STALE_MINUTES):
        return {
            **st,
            "state": "error",
            "label": f"Sin revisarse desde el {when.astimezone(_CR).strftime('%d/%m/%Y %H:%M')}: revise el servidor, el usuario y la contraseña con «Revisar buzón ahora»",
        }
    if errores:
        return {**st, "state": "error", "label": f"Revisado con {len(errores)} error(es): {errores[0]}"[:300]}
    return {**st, "state": "ok", "label": "Funcionando"}


def _aware(v) -> datetime | None:
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v)
        except ValueError:
            return None
    if not isinstance(v, datetime):
        return None
    return v if v.tzinfo else v.replace(tzinfo=UTC)
