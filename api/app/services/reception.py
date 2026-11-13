"""Responder comprobantes recibidos (aceptar / parcial / rechazar) y todo lo que cuelga de la respuesta: consecutivo
05/06/07, mensaje receptor (simulado al instante; pruebas/produccion por hacienda_sync), gasto ligado o vinculado
(R13), reintento, reapertura para produccion, descarte.

Los montos del gasto salen del XML y nunca se recalculan con una sola tarifa (facturas con tarifas mixtas u otros
cargos). Nada de costo de producto ni margen aqui (D10).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.deps import Principal
from ..models import Expense, ExpenseCategory, ReceivedDocument, ReceptionResponse, Supplier, Tenant
from ..providers.einvoice import cr_now
from ..providers.hacienda import xml44
from ..providers.hacienda.provider import InvalidXml, SimulatedHacienda
from . import expense_tax
from . import hacienda_config as hc
from .bitacora import audit_change, snapshot
from .documents import audit
from .fx import doc_rate
from .reception_calc import FISCAL_CODE, MESSAGE_CODE, CalcError, business_days_left, code_of, compute, name_of
from .sequences import next_fiscal_consecutive

BLOCKED = {
    "ajeno": "El comprobante no es para la empresa: solo se puede descartar",
    "invalido": "Hacienda rechazó este comprobante al proveedor: no se responde",
    "descartado": "El comprobante está descartado: restáurelo primero",
    "historico": "Es un comprobante histórico de Fygaro: no se responde de nuevo",
    "sin_respuesta": "Este comprobante no lleva mensaje receptor",
}
ACTION_LABEL = {"aceptada": "aceptado", "parcial": "aceptado parcialmente", "rechazada": "rechazado"}
Q5 = Decimal("0.00001")
# 05/11: en modo Pruebas la respuesta NO se envia. Las facturas del correo son de Produccion y el ambiente de Pruebas
# siempre contesta -29 ("clave no registrada"): enviarlas no prueba nada, confunde y, rechazadas, sacaban el credito del
# mes. Se guarda la decision y su mensaje armado (firmado con la llave simulada) como "ensayo", sin respuesta de Hacienda;
# despues «Enviar en Producción» manda la misma decision. La conexion se prueba con «Prueba real con Hacienda».
# False solo en las pruebas que ejercitan el envio al ambiente de Pruebas con FakeHacienda.
PRUEBAS_ENSAYO = True
ENSAYO_DETAIL = "Ensayo en Pruebas: no se envió a Hacienda"
SOLO_PRUEBAS = "Esta respuesta solo se guardó en Pruebas: use «Enviar en Producción» (confirma que no se respondió en Fygaro)"
RETRYABLE = ("rechazado", "error", "ensayo")


class RespondIn(BaseModel):
    action: str = Field(pattern="^(aceptada|parcial|rechazada)$")
    iva_condition: str = Field("credito", pattern="^(credito|credito_parcial|bienes_capital|no_credito|proporcional|0[1-5])$")
    activity_code: str | None = Field(None, max_length=10)
    detail: str | None = Field(None, min_length=5, max_length=160)  # el anexo valida minimo 5 siempre que venga
    accepted_total: Decimal | None = Field(None, ge=0)  # solo parcial
    accepted_tax: Decimal | None = Field(None, ge=0)  # solo parcial: IVA aceptado (<= iva_total)
    credit_amount: Decimal | None = Field(None, ge=0)  # 02 y 03
    create_expense: bool = True
    link_expense_id: int | None = None  # R13: vincula un gasto existente en vez de crear otro
    category_id: int | None = None
    send_now: bool = True

    @model_validator(mode="before")
    @classmethod
    def _blank_detail(cls, v):
        if isinstance(v, dict) and isinstance(v.get("detail"), str) and not v["detail"].strip():
            v = {**v, "detail": None}
        return v


def d(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x or 0))


def get_doc(db: Session, tenant_id: int, rid: int, lock: bool = False) -> ReceivedDocument:
    q = select(ReceivedDocument).where(ReceivedDocument.id == rid, ReceivedDocument.tenant_id == tenant_id)
    if lock:
        q = q.with_for_update()
    doc = db.scalar(q)
    if not doc:
        raise HTTPException(404, "Documento no encontrado")
    if lock:
        db.refresh(doc)
    return doc


def responses(db: Session, doc: ReceivedDocument) -> list[ReceptionResponse]:
    return db.scalars(select(ReceptionResponse).where(ReceptionResponse.received_document_id == doc.id).order_by(ReceptionResponse.attempt)).all()


def last_response(db: Session, doc: ReceivedDocument) -> ReceptionResponse | None:
    return db.scalar(select(ReceptionResponse).where(ReceptionResponse.received_document_id == doc.id).order_by(ReceptionResponse.attempt.desc()))


def iva_of(doc: ReceivedDocument) -> Decimal:
    return d(doc.iva_total) if doc.iva_total is not None else d(doc.tax_total)


def overdue(doc: ReceivedDocument, today: date | None = None) -> bool:
    return bool(doc.deadline and doc.deadline < (today or date.today()))


# ---------------------------------------------------------------- proveedor y gasto
def find_or_create_supplier(db: Session, tenant_id: int, doc: ReceivedDocument, user_id: int | None) -> Supplier | None:
    """Por norm_id de la cedula, incluidos archivados y papelera (se restaura); nunca un duplicado."""
    nid = xml44.norm_id(doc.issuer_id)
    if not nid:
        return None
    for s in db.scalars(select(Supplier).where(Supplier.tenant_id == tenant_id, Supplier.tax_id.is_not(None)).order_by(Supplier.id)):
        if xml44.same_id(s.tax_id, nid):
            if s.trashed_at is not None:
                s.trashed_at, s.trashed_by = None, None
                audit(db, tenant_id, user_id, "restore", "supplier", s.id, {"motivo": "comprobante recibido"})
            return s
    s = Supplier(tenant_id=tenant_id, name=(doc.issuer_name or "Proveedor")[:160], tax_id=doc.issuer_id, email=(doc.issuer_email or None))
    db.add(s)
    db.flush()
    audit(db, tenant_id, user_id, "create", "supplier", s.id, {"origen": "recepción", "clave": doc.clave})
    return s


def _rate(tax: Decimal, sub: Decimal) -> Decimal:
    if not sub:
        return Decimal(0)
    r = (abs(tax) / abs(sub) * 100).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return min(r, Decimal("999.999"))


def expense_amounts(doc: ReceivedDocument, action: str, cond: str, credit: Decimal | None, accepted_total=None, accepted_tax=None) -> dict:
    sign = Decimal(-1) if doc.doc_type == "NC" else Decimal(1)
    if action == "parcial":
        total, tax = d(accepted_total), d(accepted_tax)
    else:
        total, tax = d(doc.total), d(doc.tax_total)
    sub = total - tax
    if doc.doc_type == "TE":
        cond, credit = "no_credito", Decimal(0)
    return {
        "total": (sign * total).quantize(Q5),
        "tax_amount": (sign * tax).quantize(Q5),
        "subtotal": (sign * sub).quantize(Q5),
        "tax_rate": _rate(tax, sub),
        "tax_credit_amount": (sign * credit).quantize(Q5) if credit is not None else None,
        "iva_credit": cond,
    }


def create_expense_from_document(
    db: Session, tenant_id: int, doc: ReceivedDocument, user_id: int | None, category_id: int | None, amounts: dict, ip: str | None = None
) -> Expense:
    if category_id:
        cat = db.get(ExpenseCategory, category_id)
        if not cat or cat.tenant_id != tenant_id:
            raise HTTPException(404, "Categoría no encontrada")
    sup = find_or_create_supplier(db, tenant_id, doc, user_id)
    prefix = "Nota de crédito · " if doc.doc_type == "NC" else ""
    e = Expense(
        tenant_id=tenant_id,
        category_id=category_id,
        supplier_id=sup.id if sup else None,
        description=f"{prefix}{doc.issuer_name or 'Proveedor'} · {doc.consecutive or doc.clave[-10:]}"[:300],
        date=doc.issue_date or date.today(),
        currency=doc.currency or "CRC",
        # la tasa del XML; si no la trae, la del BCCR de su fecha (services/fx). Nunca 1 para dolares: sin tasa, 422
        fx_rate=doc_rate(db, doc.currency, doc.fx_rate, doc.issue_date),
        received_document_id=doc.id,
        received_clave=doc.clave,
        reference=doc.clave,
        created_by=user_id,
        status="registrado",
        purchase_type="contado",
        **amounts,
    )
    db.add(e)
    db.flush()
    doc.expense_id = e.id
    audit(db, tenant_id, user_id, "create", "expense", e.id, {"origen": "recepción", "clave": doc.clave}, ip=ip)
    expense_tax.sync_quietly(db, e)  # IVA por tarifa desde las lineas del XML (10/11)
    return e


def amounts_from_response(doc: ReceivedDocument) -> dict:
    """Montos del gasto segun la respuesta ya guardada (vincular despues, "Registrar gasto", historico de Fygaro).
    Parcial: el monto e IVA aceptados que se guardaron al responder; en el historico, credito + gasto aplicable del
    mensaje de Fygaro. Si no hay ninguno (parcial con 05 sin montos) -> 409: nunca se escribe un gasto en cero."""
    cond = doc.iva_condition or "credito"
    credit = d(doc.credit_amount) if doc.credit_amount is not None else None
    if doc.action == "parcial":
        if doc.accepted_total is not None:
            total, tax = d(doc.accepted_total), d(doc.accepted_tax)
        elif doc.applicable_amount is not None:
            total, tax = d(doc.applicable_amount) + (credit or 0), credit or Decimal(0)
        else:
            raise HTTPException(409, "No se conoce el monto aceptado de esta aceptación parcial: registre el gasto a mano")
        return expense_amounts(doc, "parcial", cond, credit, total, tax)
    return expense_amounts(doc, "aceptada", cond, credit if credit is not None else (iva_of(doc) if cond == "credito" else None))


def _update_linked_expense(db: Session, p: Principal, doc: ReceivedDocument, amounts: dict, action: str) -> None:
    e = db.get(Expense, doc.expense_id) if doc.expense_id else None
    if not e:
        return
    antes = snapshot(e)
    for k, v in amounts.items():
        setattr(e, k, v)
    if action == "rechazada":
        e.status = "anulado"
    elif e.status == "anulado" and doc.action == "rechazada":
        # lo anulo el rechazo anterior (doc.action todavia es la respuesta previa): la nueva lo acepta y vuelve a contar
        e.status = "registrado"
    audit_change(db, p, "update", "expense", e.id, antes, snapshot(e), reason=f"Nueva respuesta al comprobante {doc.clave}: {action}")
    expense_tax.sync_quietly(db, e)


def link_expense(db: Session, p: Principal, doc: ReceivedDocument, expense_id: int, amounts: dict) -> Expense:
    e = db.get(Expense, expense_id)
    if not e or e.tenant_id != p.tenant.id:
        raise HTTPException(404, "Gasto no encontrado")
    if e.received_document_id and e.received_document_id != doc.id:
        raise HTTPException(409, "Ese gasto ya está vinculado a otro comprobante")
    if e.status == "anulado":
        raise HTTPException(409, "El gasto está anulado")
    if doc.expense_id and doc.expense_id != e.id:
        raise HTTPException(409, "El comprobante ya tiene un gasto ligado")
    antes = snapshot(e)
    sup = find_or_create_supplier(db, p.tenant.id, doc, p.user.id)
    e.received_document_id, e.received_clave, e.reference = doc.id, doc.clave, doc.clave
    e.supplier_id = sup.id if sup else e.supplier_id
    e.currency = doc.currency or e.currency
    e.fx_rate = doc_rate(db, e.currency, doc.fx_rate, doc.issue_date)  # nunca 1 para dolares (services/fx)
    for k, v in amounts.items():
        setattr(e, k, v)
    doc.expense_id = e.id
    audit_change(db, p, "update", "expense", e.id, antes, snapshot(e), reason=f"Vinculado al comprobante {doc.clave}")
    expense_tax.sync_quietly(db, e)
    return e


def expense_candidates(db: Session, doc: ReceivedDocument) -> list[dict]:
    """Gastos que probablemente son este comprobante (un recurrente o manual registrado antes de que llegara el XML)."""
    if doc.expense_id or doc.issue_date is None:
        return []
    first = doc.issue_date.replace(day=1)
    prev_first = (first - timedelta(days=1)).replace(day=1)
    next_first = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    total = abs(d(doc.total))
    out = []
    for e in db.scalars(
        select(Expense).where(
            Expense.tenant_id == doc.tenant_id,
            Expense.received_document_id.is_(None),
            Expense.status != "anulado",
            Expense.currency == (doc.currency or "CRC"),
            Expense.date >= prev_first,
            Expense.date < next_first,
        )
    ):
        if total and abs(abs(d(e.total)) - total) > total * Decimal("0.02"):
            continue
        sup = db.get(Supplier, e.supplier_id) if e.supplier_id else None
        if sup is not None and sup.tenant_id != doc.tenant_id:
            sup = None  # un proveedor de otra empresa nunca se muestra
        if sup and not xml44.same_id(sup.tax_id, doc.issuer_id):
            continue
        out.append(
            {
                "id": e.id,
                "date": e.date,
                "description": e.description,
                "total": e.total,
                "currency": e.currency,
                "supplier": sup.name if sup else None,
                "recurrence_id": e.recurrence_id,
                "diff": abs(abs(d(e.total)) - total),
                "days": abs((e.date - doc.issue_date).days),
            }
        )
    out.sort(key=lambda c: (c["diff"], c["days"]))
    return out[:10]


# ---------------------------------------------------------------- responder
def nc_adjustment(db: Session, doc: ReceivedDocument) -> ReceivedDocument | None:
    """NC del proveedor que corrige una factura que se rechazo o se acepto parcialmente: ya no hay que restar otra vez."""
    if doc.doc_type != "NC" or not doc.reference_clave:
        return None
    ref = db.scalar(select(ReceivedDocument).where(ReceivedDocument.tenant_id == doc.tenant_id, ReceivedDocument.clave == doc.reference_clave))
    if ref and ref.action in ("rechazada", "parcial") and ref.status in ("respondido", "historico"):
        return ref
    return None


def retry_blocker(db: Session, tenant: Tenant | None, doc: ReceivedDocument, last: ReceptionResponse | None = None) -> str | None:
    """Motivo por el que un comprobante respondido NO se puede responder de nuevo (None = se puede). Nunca dos mensajes
    de Produccion vivos para la misma clave: lo de Pruebas pasa por «Enviar en Producción» (con la casilla de Fygaro) y
    un «ya fue recibido» en el primer envio (¿Fygaro?) no se responde nunca."""
    from .reception_state import error_kind

    last = last if last is not None else last_response(db, doc)
    if doc.status != "respondido" or last is None or last.status not in RETRYABLE:
        return "Solo se responde de nuevo si el último mensaje fue rechazado, dio error o fue un ensayo en Pruebas"
    tenant = tenant or db.get(Tenant, doc.tenant_id)
    if last.mode != "produccion" and hc.reception_mode(db, tenant) == "produccion":
        return SOLO_PRUEBAS
    if last.mode == "produccion" and error_kind(last) == "ya_recibido":
        return "Hacienda ya tiene una respuesta para esta clave (¿se respondió en Fygaro?): verifíquelo e importe ese mensaje con «Importar histórico»"
    return None


def can_respond(db: Session, doc: ReceivedDocument, tenant: Tenant | None = None) -> bool:
    from .reception_state import error_kind

    if doc.status == "por_revisar":
        return True
    if doc.status == "respondido":
        last = last_response(db, doc)
        if retry_blocker(db, tenant, doc, last) is not None:
            return False
        # un error de Produccion que pudo haber llegado: primero «Responder de nuevo», que consulta a Hacienda
        return not (last.mode == "produccion" and error_kind(last) == "ambiguo")
    return False


def respond(db: Session, p: Principal, rid: int, data: RespondIn) -> tuple[ReceivedDocument, ReceptionResponse, list[str]]:
    tenant: Tenant = p.tenant
    doc = get_doc(db, tenant.id, rid, lock=True)  # el estado se comprueba DESPUES del bloqueo
    if doc.status in BLOCKED:
        raise HTTPException(409, BLOCKED[doc.status])
    from .identificador import aval

    ident = aval(doc)
    if ident["bloquea"]:  # el identificador no es de esta factura: ni respuesta ni gasto (05/11)
        raise HTTPException(409, f"{ident['label']}: {ident['detalle']}. No se respondió.")
    if not can_respond(db, doc, tenant):
        raise HTTPException(409, (doc.status == "respondido" and retry_blocker(db, tenant, doc)) or f"Ya fue respondido como {doc.action or 'respondido'}")
    if doc.doc_type == "TE":
        raise HTTPException(409, BLOCKED["sin_respuesta"])
    blocked = hc.production_blocked(db, tenant)
    if blocked:
        # interruptor de produccion encendido y una llave del servidor que falla: el modo efectivo es "ninguno" y
        # reception_mode lo trataria como simulado. Un mensaje receptor simulado sobre un comprobante REAL de un
        # proveedor lo dejaria respondido sin validez fiscal (ni credito de IVA) creyendo estar en produccion
        raise HTTPException(409, f"Producción está activada pero este servidor no la permite ({blocked}): no se respondió. Revise Ajustes → Hacienda")
    if data.action in ("parcial", "rechazada") and not data.detail:
        raise HTTPException(422, "El detalle es obligatorio (5 a 160 caracteres) en aceptación parcial y rechazo")
    if data.link_expense_id and data.action == "rechazada":
        raise HTTPException(422, "Un rechazo no se vincula a un gasto")
    try:
        cond = name_of(data.iva_condition)
    except CalcError as e:
        raise HTTPException(422, str(e)) from e
    activity = None
    if data.action != "rechazada" and code_of(cond) != "05":
        activity = (data.activity_code or "").strip() or None
        if activity is None:
            act = hc.default_activity(db, tenant.id)
            if not act:
                raise HTTPException(422, "Configure una actividad económica por defecto en Ajustes → Hacienda")
            activity = act.code
        elif not hc.active_activity(db, tenant.id, activity):
            raise HTTPException(422, f"La actividad {activity} no está activa en Ajustes → Hacienda")
    elif data.action != "rechazada" and data.activity_code:
        activity = data.activity_code  # con 05 no viaja en el XML, pero se guarda
    try:
        calc = compute(
            data.action,
            cond,
            d(doc.tax_total),
            iva_of(doc),
            d(doc.total),
            data.accepted_total if data.action == "parcial" else None,
            data.accepted_tax if data.action == "parcial" else None,
            data.credit_amount,
        )
    except CalcError as e:
        raise HTTPException(422, str(e)) from e
    warnings = []
    if overdue(doc):
        warnings.append("Fuera de plazo: Hacienda presume la aceptación total y puede rechazar el mensaje")
    mode = hc.reception_mode(db, tenant)
    ensayo = mode == "pruebas" and PRUEBAS_ENSAYO
    attempt = (db.scalar(select(func.max(ReceptionResponse.attempt)).where(ReceptionResponse.received_document_id == doc.id)) or 0) + 1
    consecutive = next_fiscal_consecutive(db, tenant.id, FISCAL_CODE[data.action], mode)
    r = ReceptionResponse(
        tenant_id=tenant.id,
        received_document_id=doc.id,
        attempt=attempt,
        message_code=MESSAGE_CODE[data.action],
        iva_condition_code=code_of(cond) if data.action != "rechazada" else None,
        activity_code=activity,
        tax_total=abs(d(doc.tax_total)),
        total=abs(d(doc.total)),
        credit_amount=abs(calc.credit) if calc.credit is not None else None,
        applicable_amount=abs(calc.applicable) if calc.applicable is not None else None,
        detail=data.detail,
        consecutive=consecutive,
        mode=mode,
        emitted_at=cr_now(),
        status="simulado" if mode == "simulado" else ("ensayo" if ensayo else "por_enviar"),
        created_by=p.user.id,
        created_at=datetime.now(UTC),
    )
    if mode == "simulado" or ensayo:
        try:
            sim = SimulatedHacienda()
            r.xml_signed = sim.sign_receptor(xml44.build_mensaje_receptor(r, doc, tenant, xml44.digits(tenant.tax_id) or "000000000"))
            r.xml_signed = r.xml_signed.decode("utf-8")
            if ensayo:
                # sin respuesta falsa de Hacienda: el ensayo nunca salio (hacienda_xml queda vacio)
                r.hacienda_xml, r.hacienda_detail = None, ENSAYO_DETAIL
            else:
                r.hacienda_xml = xml44.mensaje_hacienda_simulado(doc.clave, doc.issuer_id or "", xml44.digits(tenant.tax_id), doc.tax_total, doc.total)
                r.hacienda_detail = "SIMULACIÓN · sin validez fiscal"
        except (xml44.BuildError, InvalidXml) as e:
            db.rollback()
            raise HTTPException(422, f"No se pudo armar el mensaje receptor: {e}") from e
    sp = db.begin_nested()
    try:
        db.add(r)
        db.flush()
        sp.commit()
    except IntegrityError as e:
        sp.rollback()
        db.rollback()  # tambien se devuelve el consecutivo: no se gastan dos
        same = (ReceptionResponse.tenant_id == tenant.id, ReceptionResponse.mode == mode, ReceptionResponse.consecutive == consecutive)
        if db.scalar(select(ReceptionResponse.id).where(*same)):
            # no es un doble clic: la numeracion choca con un mensaje que ya existe (p. ej. el historico de Fygaro)
            raise HTTPException(
                409,
                f"El consecutivo {consecutive} ya lo tiene otro mensaje receptor (¿histórico de Fygaro?): "
                "ajuste el consecutivo en Ajustes → Hacienda → Consecutivos",
            ) from e
        raise HTTPException(409, "Ya fue respondido (otra persona lo respondió al mismo tiempo)") from e
    # gasto
    amounts = expense_amounts(doc, data.action, cond, calc.credit, data.accepted_total, data.accepted_tax) if data.action != "rechazada" else None
    if doc.expense_id:
        if amounts is None:
            e = db.get(Expense, doc.expense_id)
            if e:
                antes = snapshot(e)
                e.status = "anulado"
                audit_change(db, p, "update", "expense", e.id, antes, snapshot(e), reason=f"Comprobante {doc.clave} rechazado")
        else:
            _update_linked_expense(db, p, doc, amounts, data.action)
    elif data.link_expense_id:
        link_expense(db, p, doc, data.link_expense_id, amounts)
    elif data.create_expense and amounts is not None:
        create_expense_from_document(db, tenant.id, doc, p.user.id, data.category_id, amounts, ip=p.ip)
    doc.iva_condition = cond
    doc.activity_code = activity
    doc.credit_amount = calc.credit
    doc.applicable_amount = calc.applicable
    # parcial: se guarda lo aceptado (con 05 no hay credito ni gasto aplicable de donde reconstruirlo despues)
    doc.accepted_total = data.accepted_total if data.action == "parcial" else None
    doc.accepted_tax = data.accepted_tax if data.action == "parcial" else None
    doc.response_detail = data.detail
    doc.action = data.action
    doc.status = "respondido"
    doc.responded_by, doc.responded_at = p.user.id, datetime.now(UTC)
    doc.hacienda_status = r.status
    if doc.expense_id and data.action != "rechazada":
        # otra vez con la respuesta ya guardada: una aceptacion parcial escala las tarifas a lo aceptado
        expense_tax.sync_quietly(db, db.get(Expense, doc.expense_id))
    audit(
        db,
        tenant.id,
        p.user.id,
        "reception_respond",
        "received_document",
        doc.id,
        {"action": data.action, "condicion": cond, "consecutivo": consecutive, "modo": mode, "intento": attempt},
        ip=p.ip,
    )
    db.commit()
    if data.send_now and r.status == "por_enviar":
        from .hacienda_sync import send_response

        try:
            send_response(db, r.id)  # fuera de la transaccion anterior: errores de red no deshacen la respuesta
        except Exception:  # noqa: BLE001
            db.rollback()
        db.refresh(doc)
        db.refresh(r)
    return doc, r, warnings


def expense_for_document(db: Session, p: Principal, doc: ReceivedDocument, category_id: int | None) -> Expense:
    """POST /reception/{id}/expense: gasto de un tiquete o de uno respondido sin gasto."""
    if doc.expense_id:
        raise HTTPException(409, "El comprobante ya tiene gasto")
    if doc.doc_type == "TE" or (doc.status == "ajeno" and not doc.receiver_id):
        amounts = expense_amounts(doc, "aceptada", "no_credito", Decimal(0))
        amounts["iva_credit"] = "no_credito"
        amounts["tax_credit_amount"] = Decimal(0)
    elif doc.status in ("respondido", "historico") and doc.action in ("aceptada", "parcial"):
        amounts = amounts_from_response(doc)
    else:
        raise HTTPException(409, "Solo se registra gasto de un tiquete o de un comprobante aceptado")
    e = create_expense_from_document(db, p.tenant.id, doc, p.user.id, category_id, amounts, ip=p.ip)
    return e


def retry(db: Session, p: Principal, doc: ReceivedDocument) -> None:
    from .reception_state import error_kind

    last = last_response(db, doc)
    why = retry_blocker(db, p.tenant, doc, last)
    if why:
        raise HTTPException(409, why)
    if last.mode == "produccion" and error_kind(last) == "ambiguo":
        # pudo haber llegado: se consulta a Hacienda antes de crear un segundo mensaje con otro consecutivo
        from .hacienda_sync import verify_response

        kind = verify_response(db, last)
        if kind == "aceptado":
            db.commit()
            raise HTTPException(409, "Hacienda sí recibió esa respuesta y la aprobó: no se responde de nuevo")
        if kind not in ("not_found", "rechazado"):
            db.commit()
            raise HTTPException(409, "No se pudo confirmar con Hacienda si la respuesta llegó: inténtelo más tarde")
    doc.status = "por_revisar"
    audit(db, p.tenant.id, p.user.id, "reception_retry", "received_document", doc.id, {"intento_anterior": last.attempt, "estado": last.status}, ip=p.ip)


def legacy_simulated(doc: ReceivedDocument, rs: list[ReceptionResponse]) -> bool:
    """Respondido antes de la migracion: sin filas en reception_response, su mensaje heredado vive en xml_response y
    el relleno lo dejo en hacienda_status=simulado (nunca se envio a Hacienda)."""
    return doc.status == "respondido" and not rs and doc.hacienda_status == "simulado"


def reopen_blocker(db: Session, tenant: Tenant, doc: ReceivedDocument, rs: list[ReceptionResponse] | None = None) -> str | None:
    """Motivo por el que NO se puede reabrir para produccion (None = se puede)."""
    blocked = hc.production_blocked(db, tenant)
    if blocked:
        return f"Producción está activada pero este servidor no la permite ({blocked}): se reabre cuando vuelva a estar disponible"
    if hc.reception_mode(db, tenant) != "produccion":
        return "Reabrir es solo para pasar a producción lo que se probó antes del cambio"
    if doc.status == "historico" or doc.origin == "historico":
        return "Es un comprobante histórico de Fygaro: no se reabre"
    if overdue(doc):
        return "El plazo para responder ya venció"
    rs = responses(db, doc) if rs is None else rs
    if any(r.mode == "produccion" or r.status == "historico" for r in rs):
        return "Ya tiene un mensaje de producción o histórico"
    if doc.status != "respondido" or not (rs or legacy_simulated(doc, rs)):
        return "Solo se reabre un comprobante respondido en simulado o pruebas"
    return None


def reopen(db: Session, p: Principal, doc: ReceivedDocument, confirm: bool) -> None:
    if not confirm:
        raise HTTPException(422, "Confirme que este comprobante NO se respondió en Fygaro")
    why = reopen_blocker(db, p.tenant, doc)
    if why:
        raise HTTPException(409, why)
    from .hacienda_sync import close_superseded

    # el mensaje de pruebas que siga por enviar o en consulta se cierra: si no, el worker lo terminaria despues y su
    # resultado ("Cambió el modo" o la respuesta del ambiente de pruebas) pisaria el estado del mensaje de produccion
    cerrados = close_superseded(db, doc.id, "Reemplazado: se reabrió para responder en producción")
    doc.status = "por_revisar"
    doc.hacienda_status = "sin_respuesta"  # aun no tiene mensaje de produccion
    audit(db, p.tenant.id, p.user.id, "reception_reopen", "received_document", doc.id, {"confirmo_no_en_fygaro": True, "mensajes_cerrados": cerrados}, ip=p.ip)


def production_input(doc: ReceivedDocument) -> RespondIn:
    """La misma decision que ya se guardo (en Pruebas o simulado), lista para pasar otra vez por respond()."""
    if not doc.action:
        raise HTTPException(409, "No hay una decisión guardada: use «Cambiar la respuesta antes de enviarla»")
    cond = doc.iva_condition or "credito"
    return RespondIn(
        action=doc.action,
        iva_condition=cond,
        activity_code=doc.activity_code,
        detail=doc.response_detail,
        accepted_total=doc.accepted_total if doc.action == "parcial" else None,
        accepted_tax=doc.accepted_tax if doc.action == "parcial" else None,
        credit_amount=doc.credit_amount if code_of(cond) in ("02", "03") else None,
        create_expense=False,  # el gasto que ya existe se reutiliza (respond lo actualiza): nunca uno doble
        send_now=True,
    )


def send_in_production(db: Session, p: Principal, doc: ReceivedDocument, confirm: bool) -> tuple[ReceivedDocument, ReceptionResponse, list[str]]:
    """«Enviar en Producción»: la decision guardada en Pruebas (o simulado) sale a Hacienda sin responderla a mano otra
    vez. Mismas reglas que reabrir (plazo, historico, sin otro mensaje de Produccion) y la casilla de Fygaro. Pasa por
    respond() completo (actividad activa, produccion bloqueada, calculo) y el intento nuevo nace SIN firmar: lo firma
    send_response con el .p12 de PRODUCCION (el del ensayo va con la llave simulada y nunca viaja)."""
    if not confirm:
        raise HTTPException(422, "Confirme que este comprobante NO se respondió en Fygaro")
    rs = responses(db, doc)
    why = reopen_blocker(db, p.tenant, doc, rs)
    if why:
        raise HTTPException(409, why)
    data = production_input(doc)
    from .hacienda_sync import close_superseded

    cerrados = close_superseded(db, doc.id, "Reemplazado: se envió en Producción")
    doc.status = "por_revisar"  # respond() lo exige; si algo falla se deshace (rollback)
    db.flush()
    try:
        doc, r, warnings = respond(db, p, doc.id, data)
    except HTTPException:
        db.rollback()
        raise
    audit(
        db,
        p.tenant.id,
        p.user.id,
        "reception_send_production",
        "received_document",
        doc.id,
        {"confirmo_no_en_fygaro": True, "mensajes_cerrados": cerrados, "intento": r.attempt},
        ip=p.ip,
    )
    db.commit()
    return doc, r, warnings


def discard(db: Session, p: Principal, doc: ReceivedDocument, reason: str) -> None:
    if doc.status not in ("por_revisar", "ajeno", "invalido", "sin_respuesta"):
        raise HTTPException(409, "Solo se descarta lo que no tiene mensaje ante Hacienda (lo respondido y lo histórico se conservan)")
    if doc.expense_id:
        e = db.get(Expense, doc.expense_id)
        if e and e.status != "anulado":
            raise HTTPException(409, "Anule el gasto primero")
    doc.status_before_discard = doc.status
    doc.status = "descartado"
    doc.discarded_reason, doc.discarded_by, doc.discarded_at = reason, p.user.id, datetime.now(UTC)
    audit(db, p.tenant.id, p.user.id, "reception_discard", "received_document", doc.id, {"motivo": reason}, ip=p.ip)


def restore(db: Session, p: Principal, doc: ReceivedDocument) -> None:
    if doc.status != "descartado":
        raise HTTPException(409, "El comprobante no está descartado")
    doc.status = doc.status_before_discard or "por_revisar"
    doc.status_before_discard = None
    doc.discarded_reason, doc.discarded_by, doc.discarded_at = None, None, None
    audit(db, p.tenant.id, p.user.id, "reception_restore", "received_document", doc.id, {"estado": doc.status}, ip=p.ip)


def days_left(doc: ReceivedDocument) -> int | None:
    return business_days_left(date.today(), doc.deadline)
