"""Envio y consulta ante Hacienda, compartido por la recepcion (mensajes receptor) y la emision (FE/TE/NC).

1. Reclamo atomico (UPDATE ... WHERE status='por_enviar' + commit): el worker y "Enviar ahora" nunca mandan lo mismo
   dos veces (un SELECT FOR UPDATE no sirve: el POST dura hasta 20 s y no puede ir dentro de la transaccion).
2. Rechazos previos al POST: simulado nunca sale; modo cambiado -> error; produccion sin las tres llaves -> error;
   credenciales faltantes, ilegibles o certificado vencido -> vuelve a por_enviar con last_error.
3. Se firma UNA vez y se guarda; con el token ya en mano (un fallo del IdP no cuenta como POST), attempts += 1 y
   commit ANTES del POST (D6): un reintento manda los mismos bytes.
4. "ya fue recibido" solo cuenta como enviado si hubo un POST nuestro anterior (attempts > 1).
5. Consulta a 1, 2, 5, 10, 20, 30 min y luego cada hora; 72 h sin respuesta final -> error.
6. Mensajes receptor: solo el ultimo intento de cada recepcion se envia, se consulta y pone el estado de la recepcion;
   los anteriores sin estado final se cierran al reabrir o al importar el de Fygaro (close_superseded).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ..models import EInvoiceDocument, FiscalCredential, Invoice, ReceivedDocument, ReceptionResponse, Tenant
from ..providers.hacienda import xml44
from ..providers.hacienda.client import HaciendaAuthError, HaciendaUnavailable, ProductionBlocked, production_keys, rate_limited
from ..providers.hacienda.provider import HaciendaDirectProvider, InvalidXml, comprobante_body, receptor_body, receptor_ref
from ..providers.hacienda.xades import CertError
from . import hacienda_config as hc
from .documents import audit

log = logging.getLogger("crimson.hacienda")
SEND_BACKOFF = (1, 2, 5, 10, 30, 60)  # minutos; despues cada 60
POLL_SCHEDULE = (1, 2, 5, 10, 20, 30)  # minutos desde el envio; despues cada 60
STUCK = timedelta(minutes=10)
GIVE_UP = timedelta(hours=72)
FINAL_RESPONSE = ("aceptado", "rechazado", "error", "simulado", "historico")


def now() -> datetime:
    return datetime.now(UTC)


def aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _backoff(attempts: int) -> timedelta:
    i = max(attempts - 1, 0)
    return timedelta(minutes=SEND_BACKOFF[i] if i < len(SEND_BACKOFF) else 60)


def _next_poll(sent_at: datetime | None, polls: int) -> datetime:
    base = aware(sent_at) or now()
    if polls < len(POLL_SCHEDULE):
        at = base + timedelta(minutes=POLL_SCHEDULE[polls])
        return max(at, now() + timedelta(seconds=30))
    return now() + timedelta(minutes=60)


def _claim(db: Session, model, row_id: int) -> bool:
    t = now()
    res = db.execute(
        update(model)
        .where(model.id == row_id, model.status == "por_enviar", or_(model.next_attempt_at.is_(None), model.next_attempt_at <= t))
        .values(status="enviando", claimed_at=t)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return res.rowcount == 1


def _cert_expired(db: Session, tenant_id: int, env: str) -> bool:
    row = db.scalar(select(FiscalCredential).where(FiscalCredential.tenant_id == tenant_id, FiscalCredential.environment == env))
    na = aware(row.cert_not_after) if row else None
    return bool(na and na <= now())


def _env(mode: str) -> str:
    return "produccion" if mode == "produccion" else "pruebas"


def _provider(db: Session, tenant: Tenant, mode: str):
    """(proveedor, error_para_reintentar, error_final)."""
    env = _env(mode)
    if mode == "produccion":
        ok, why = production_keys(hc.profile(db, tenant).production_instance)
        if not ok:
            return None, None, why or "El servidor no permite producción"
    creds, err = hc.credentials_status(db, tenant, env)
    if not creds:
        return None, err, None
    if _cert_expired(db, tenant.id, env):
        return None, "Certificado vencido: cargue el nuevo .p12", None
    try:
        return HaciendaDirectProvider(tenant, env, creds, production_instance=hc.profile(db, tenant).production_instance), None, None
    except ProductionBlocked as e:
        return None, None, str(e)


# ================================================================ mensajes receptor
OPEN_RESPONSE = ("por_enviar", "enviando", "enviado", "procesando")
SUPERSEDED = "Reemplazado por un mensaje posterior"


def _superseded(db: Session, r: ReceptionResponse) -> bool:
    """Hay un intento posterior de la misma recepcion (respondida de nuevo tras reabrir, o el historico de Fygaro)."""
    last = db.scalar(select(func.max(ReceptionResponse.attempt)).where(ReceptionResponse.received_document_id == r.received_document_id))
    return (r.attempt or 0) < (last or 0)


def close_superseded(db: Session, received_document_id: int, why: str) -> int:
    """Cierra (error, con el motivo) los mensajes de esa recepcion que aun no tienen estado final, para que el worker deje
    de enviarlos y de consultarlos: al reabrir para produccion (lo de pruebas no sigue vivo junto al mensaje nuevo) y al
    importar el mensaje de Fygaro (gana Fygaro, §6.9). Devuelve cuantos cerro."""
    rows = db.scalars(
        select(ReceptionResponse).where(ReceptionResponse.received_document_id == received_document_id, ReceptionResponse.status.in_(OPEN_RESPONSE))
    ).all()
    for r in rows:
        _fail(r, why)
        r.next_attempt_at = None
    return len(rows)


def _copy_response_status(db: Session, r: ReceptionResponse) -> None:
    """Solo el ULTIMO intento manda en el estado de la recepcion: uno viejo que el worker cierra o consulta despues (el de
    pruebas tras reabrir, o uno que reemplazo el historico de Fygaro) no pisa el del mensaje vigente."""
    if _superseded(db, r):
        return
    doc = db.get(ReceivedDocument, r.received_document_id)
    if doc is not None:
        doc.hacienda_status = r.status


def _retry_later(r, msg: str, minutes: int = 30) -> None:
    r.status = "por_enviar"
    r.last_error = (msg or "")[:300]
    r.next_attempt_at = now() + timedelta(minutes=minutes)
    r.claimed_at = None


def _fail(r, msg: str) -> None:
    r.status = "error"
    r.last_error = (msg or "")[:300]
    r.claimed_at = None
    r.resolved_at = now()  # estado final: la alerta diaria toma los errores de las ultimas 24 h


def _auth_retry(prov, row) -> bool:
    """Token ANTES de contar el intento (§5.7: attempts = POST reales). Un IdP caido o una contrasena ATV mala no
    mandan nada a Hacienda: la fila vuelve a por_enviar SIN sumar attempts (si no, al corregir las credenciales el
    primer POST real ya llevaria attempts=2 y un "ya fue recibido" de Fygaro pasaria por exito). True = no se envia."""
    try:
        prov.authenticate()
    except HaciendaAuthError as e:
        _retry_later(row, str(e))
        return True
    except HaciendaUnavailable as e:
        _retry_later(row, str(e), minutes=int(_backoff(max(row.attempts or 0, 1)).total_seconds() // 60))
        return True
    return False


def _undo_attempt(row) -> None:
    """Hacienda rechazo la autenticacion del POST (401 aun con token renovado): el documento no llego."""
    row.attempts = max((row.attempts or 1) - 1, 0)


def send_response(db: Session, response_id: int) -> str | None:
    r = db.get(ReceptionResponse, response_id)
    if r is None:
        return None
    if r.status != "por_enviar" or not _claim(db, ReceptionResponse, response_id):
        db.refresh(r)
        return r.status
    db.refresh(r)
    doc = db.get(ReceivedDocument, r.received_document_id)
    tenant = db.get(Tenant, r.tenant_id)
    if _superseded(db, r):
        _fail(r, SUPERSEDED)  # ya hay otro mensaje para esta recepcion: este no sale
        db.commit()
        return r.status
    if r.mode == "simulado":
        _fail(r, "Un mensaje simulado nunca se envía a Hacienda")
    elif r.mode != hc.reception_mode(db, tenant):
        _fail(r, "Cambió el modo: responder de nuevo")
    if r.status == "error":
        _copy_response_status(db, r)
        db.commit()
        return r.status
    prov, retry_err, final_err = _provider(db, tenant, r.mode)
    if final_err:
        _fail(r, final_err)
    elif retry_err:
        _retry_later(r, retry_err)
    if prov is None:
        _copy_response_status(db, r)
        db.commit()
        return r.status
    receiver_digits = xml44.digits(tenant.tax_id)
    if not r.xml_signed:
        try:
            signed = prov.sign_receptor(xml44.build_mensaje_receptor(r, doc, tenant, receiver_digits))
        except (xml44.BuildError, InvalidXml) as e:
            _fail(r, f"El mensaje no pasa el esquema (bug, no se envía): {e}")
            _copy_response_status(db, r)
            db.commit()
            return r.status
        except CertError as e:
            _retry_later(r, str(e))
            _copy_response_status(db, r)
            db.commit()
            return r.status
        r.xml_signed = signed.decode("utf-8")
    if _auth_retry(prov, r):
        _copy_response_status(db, r)
        db.commit()
        return r.status
    r.attempts = (r.attempts or 0) + 1
    r.sent_at = now()
    db.commit()  # constancia ANTES del POST: si el proceso muere durante el POST, sabemos que pudo haber llegado
    body = receptor_body(r, doc, hc.profile(db, tenant).id_type or "02", receiver_digits, r.xml_signed.encode("utf-8"))
    try:
        res = prov.post(body)
    except HaciendaAuthError as e:
        _undo_attempt(r)
        _retry_later(r, str(e))
        _copy_response_status(db, r)
        db.commit()
        return r.status
    _apply_post(r, res, ref=receptor_ref(doc, r), first_msg="Hacienda ya tiene un documento con esa clave: ¿se respondió o emitió desde Fygaro?")
    _copy_response_status(db, r)
    db.commit()
    return r.status


def _apply_post(row, res, *, ref: str, first_msg: str) -> None:
    if res.kind == "ok" or (res.kind == "already_received" and (row.attempts or 0) > 1):
        row.status = "enviado"
        row.provider_ref = (res.location or ref)[:200] if hasattr(row, "provider_ref") else None
        row.last_error = None
        row.claimed_at = None
        row.next_attempt_at = _next_poll(row.sent_at, 0)
    elif res.kind == "already_received":
        _fail(row, first_msg)
    elif res.kind == "retry":
        row.status = "por_enviar"
        row.claimed_at = None
        row.last_error = (res.cause or "Sin respuesta de Hacienda")[:300]
        row.next_attempt_at = now() + (timedelta(seconds=res.retry_after) if res.retry_after else _backoff(row.attempts or 1))
    else:
        _fail(row, res.cause or "Hacienda rechazó el envío")


def refresh_response(db: Session, response_id: int) -> str | None:
    r = db.get(ReceptionResponse, response_id)
    if r is None or r.status not in ("enviado", "procesando", "enviando"):
        return r.status if r else None
    if _superseded(db, r):
        _fail(r, SUPERSEDED)  # su resultado ya no cuenta: se deja de consultar
        db.commit()
        return r.status
    doc = db.get(ReceivedDocument, r.received_document_id)
    tenant = db.get(Tenant, r.tenant_id)
    prov, retry_err, final_err = _provider(db, tenant, r.mode)
    if prov is None:
        r.last_error = (final_err or retry_err or "")[:300]
        r.next_attempt_at = now() + timedelta(minutes=30)
        db.commit()
        return r.status
    try:
        st = prov.get_status(receptor_ref(doc, r))
    except HaciendaAuthError as e:
        r.last_error = str(e)[:300]
        db.commit()
        return r.status
    _apply_status(r, st, final_ok="aceptado", final_bad="rechazado")
    _copy_response_status(db, r)
    db.commit()
    return r.status


def _apply_status(row, st, *, final_ok: str, final_bad: str) -> None:
    polls = getattr(row, "_polls", None)
    sent = aware(row.sent_at)
    if st.kind == "not_found":
        if row.status == "enviando" or (row.attempts or 0) == 0:
            row.status = "por_enviar"  # nunca llego: se reintenta con los MISMOS bytes
            row.claimed_at = None
            row.next_attempt_at = None
            return
        if sent and now() - sent < STUCK:
            row.status = "procesando"
        else:
            _fail(row, "Hacienda no encuentra el documento")
            return
    elif st.kind == "procesando":
        row.status = "procesando"
    elif st.kind in ("aceptado", "rechazado"):
        row.status = final_ok if st.kind == "aceptado" else final_bad
        row.hacienda_xml = st.xml if hasattr(row, "hacienda_xml") else None
        if hasattr(row, "hacienda_detail"):
            row.hacienda_detail = (st.detail or "")[:500] or None
        row.resolved_at = now()
        row.claimed_at = None
        return
    elif st.kind == "error":
        row.status = "error"
        if hasattr(row, "hacienda_xml"):
            row.hacienda_xml = st.xml
            row.hacienda_detail = (st.detail or "Hacienda devolvió error")[:500]
        row.last_error = (st.detail or "Hacienda devolvió error")[:300]
        row.resolved_at = now()
        return
    elif row.status == "enviando":  # retry sobre una fila atascada: se deja para la proxima vuelta
        return
    if sent and now() - sent > GIVE_UP and row.status not in ("aceptado", "rechazado", "aceptada", "rechazada"):
        _fail(row, "Sin respuesta de Hacienda en 72 h")
        return
    n = polls if polls is not None else _poll_count(row)
    row.next_attempt_at = _next_poll(row.sent_at, n)


def _poll_count(row) -> int:
    sent = aware(row.sent_at)
    if not sent:
        return 0
    mins = (now() - sent).total_seconds() / 60
    return sum(1 for m in POLL_SCHEDULE if m <= mins)


# ================================================================ comprobantes emitidos
def _copy_invoice_status(db: Session, doc: EInvoiceDocument, *, user_id: int | None = None) -> None:
    inv = db.get(Invoice, doc.invoice_id)
    if inv is None:
        return
    if doc.doc_type == "NC":
        if doc.status == "aceptada":
            _void_after_nc(db, inv, doc, user_id)
        return
    if doc.status in ("por_enviar", "enviando", "enviado", "procesando"):
        inv.einvoice_status = "pendiente"
    elif doc.status == "aceptada":
        inv.einvoice_status = "aceptada"
        inv.clave = doc.clave
    elif doc.status == "rechazada":
        inv.einvoice_status = "rechazada"
    elif doc.status == "error":
        # §6.5.4 paso 5: sin ningun POST nunca salio -> sin_emitir. Con POST (403, "no encuentra el documento", 72 h sin
        # respuesta...) Hacienda NO lo rechazo y pudo haberlo aceptado: "error" (sigue en el IVA debito; reemitir es una
        # decision de quien revisa, no algo automatico). "rechazada" queda solo para el rechazo real de Hacienda.
        inv.einvoice_status = "sin_emitir" if (doc.attempts or 0) == 0 else "error"


def _void_after_nc(db: Session, inv: Invoice, doc: EInvoiceDocument, user_id: int | None) -> None:
    """NC aceptada: anula la factura y devuelve el inventario UNA sola vez (marca en la bitacora)."""
    from ..models import AuditLog
    from . import inventory

    if inv.status == "anulada":
        return
    done = db.scalar(select(AuditLog.id).where(AuditLog.tenant_id == inv.tenant_id, AuditLog.entity == "invoice", AuditLog.entity_id == inv.id, AuditLog.action == "nc_restock"))
    inv.status = "anulada"
    audit(db, inv.tenant_id, user_id, "void_nc", "invoice", inv.id, {"nc_clave": doc.clave, "modo": doc.mode})
    if not done:
        inventory.restock_for_void(db, inv, user_id)
        audit(db, inv.tenant_id, user_id, "nc_restock", "invoice", inv.id, {"nc_clave": doc.clave})
        from . import commissions

        commissions.on_invoice_void(db, inv, user_id)  # lo devengado con la factura se anula o se revierte (falla E)


def send_einvoice(db: Session, doc_id: int) -> str | None:
    doc = db.get(EInvoiceDocument, doc_id)
    if doc is None:
        return None
    if doc.status != "por_enviar" or not _claim(db, EInvoiceDocument, doc_id):
        db.refresh(doc)
        return doc.status
    db.refresh(doc)
    tenant = db.get(Tenant, doc.tenant_id)
    if doc.mode not in ("pruebas", "produccion"):
        _fail(doc, "Un comprobante simulado nunca se envía a Hacienda")
    elif doc.mode != hc.hacienda_mode(db, tenant):
        _fail(doc, "Cambió el modo: emitir de nuevo")
    if doc.status == "error":
        doc.hacienda_message = doc.last_error
        _copy_invoice_status(db, doc)
        db.commit()
        return doc.status
    prov, retry_err, final_err = _provider(db, tenant, doc.mode)
    if final_err:
        _fail(doc, final_err)
    elif retry_err:
        _retry_later(doc, retry_err)
    if prov is None:
        doc.hacienda_message = doc.last_error
        _copy_invoice_status(db, doc)
        db.commit()
        return doc.status
    if not doc.xml_document:  # emit() firma al emitir; esto solo cubre filas viejas o recuperadas
        _fail(doc, "Comprobante sin XML firmado: emitir de nuevo")
        _copy_invoice_status(db, doc)
        db.commit()
        return doc.status
    if _auth_retry(prov, doc):
        doc.hacienda_message = doc.last_error
        _copy_invoice_status(db, doc)
        db.commit()
        return doc.status
    doc.attempts = (doc.attempts or 0) + 1
    doc.sent_at = now()
    db.commit()
    root = xml44.parse_xml_bytes(doc.xml_document.encode("utf-8"))
    payload = _payload_from_xml(root, doc)
    try:
        res = prov.post(comprobante_body(payload, doc.xml_document.encode("utf-8")))
    except HaciendaAuthError as e:
        _undo_attempt(doc)
        _retry_later(doc, str(e))
        doc.hacienda_message = doc.last_error
        _copy_invoice_status(db, doc)
        db.commit()
        return doc.status
    _apply_post(doc, res, ref=doc.clave, first_msg="Hacienda ya tiene un comprobante con esa clave: revise si se emitió desde Fygaro")
    if res.kind == "ok" or doc.status == "enviado":
        doc.provider_ref = doc.clave
    doc.hacienda_message = doc.last_error if doc.status in ("error", "por_enviar") else "Enviado a Hacienda"
    _copy_invoice_status(db, doc)
    db.commit()
    if doc.status in ("enviado", "procesando"):
        # art. 18: al cliente "aun sin confirmacion", pero solo lo que Hacienda RECIBIO. Un 400/403 o un "ya fue
        # recibido" en el primer intento dejan el comprobante en error (se reemite con otra clave): mandarlo dejaria al
        # cliente con dos XML para la misma factura. Lo que quedo por_enviar lo entrega send_pending al salir.
        _deliver_safe(db, doc.id, "emitido")
    return doc.status


def _payload_from_xml(root, doc: EInvoiceDocument) -> dict:
    """Lo minimo del cuerpo del POST sacado del propio XML firmado (emisor, receptor y fecha exactos)."""
    em, rc = xml44.child(root, "Emisor"), xml44.child(root, "Receptor")
    p = {
        "clave": doc.clave,
        "fecha": xml44.parse_dt(xml44.text(root, "FechaEmision")) or doc.emitted_at,
        "emisor": {"id_type": xml44.text(em, "Identificacion", "Tipo"), "id_number": xml44.text(em, "Identificacion", "Numero")},
        "receptor": None,
    }
    if rc is not None:
        p["receptor"] = {"id_type": xml44.text(rc, "Identificacion", "Tipo"), "id_number": xml44.text(rc, "Identificacion", "Numero")}
    return p


def refresh_einvoice(db: Session, doc_id: int) -> str | None:
    doc = db.get(EInvoiceDocument, doc_id)
    if doc is None or doc.status not in ("enviado", "procesando", "enviando") or doc.mode not in ("pruebas", "produccion"):
        return doc.status if doc else None
    tenant = db.get(Tenant, doc.tenant_id)
    prov, retry_err, final_err = _provider(db, tenant, doc.mode)
    if prov is None:
        doc.last_error = (final_err or retry_err or "")[:300]
        doc.next_attempt_at = now() + timedelta(minutes=30)
        db.commit()
        return doc.status
    try:
        st = prov.get_status(doc.clave)
    except HaciendaAuthError as e:
        doc.last_error = str(e)[:300]
        db.commit()
        return doc.status
    _apply_status(doc, st, final_ok="aceptada", final_bad="rechazada")
    if doc.status in ("aceptada", "rechazada", "error"):
        doc.xml_response = st.xml or doc.xml_response
        doc.hacienda_message = (st.detail or doc.last_error or doc.status)[:500]
    _copy_invoice_status(db, doc)
    if doc.status == "rechazada" and doc.doc_type == "NC":
        _alert_admins(db, tenant, f"Hacienda rechazó la nota de crédito {doc.consecutive}", f"La factura queda como estaba. Detalle: {doc.hacienda_message or ''}")
    db.commit()
    if doc.status in ("aceptada", "rechazada"):
        _deliver_safe(db, doc.id, "hacienda")
    return doc.status


def _deliver_safe(db: Session, doc_id: int, stage: str) -> None:
    from .einvoice_delivery import deliver

    try:
        if deliver(db, doc_id, stage):
            db.commit()
        else:
            db.commit()
    except Exception as e:  # noqa: BLE001 - la entrega nunca tumba el envio; el worker la reintenta
        db.rollback()
        log.warning("entrega %s del comprobante %s: %s", stage, doc_id, type(e).__name__)


def _alert_admins(db: Session, tenant: Tenant, subject: str, text: str) -> None:
    from html import escape

    from .mail import notify_roles

    notify_roles(db, tenant, ("admin",), subject, f"<p>{escape(text)}</p>", "hacienda", 0)


# ================================================================ lotes del worker
def send_pending(db: Session, limit: int = 20) -> dict:
    t = now()
    out = {"respuestas": 0, "comprobantes": 0, "entregas": 0}
    cond = lambda m: (m.status == "por_enviar") & or_(m.next_attempt_at.is_(None), m.next_attempt_at <= t)  # noqa: E731
    for rid in db.scalars(select(ReceptionResponse.id).where(cond(ReceptionResponse)).order_by(ReceptionResponse.id).limit(limit)).all():
        if rate_limited():
            break  # Hacienda dijo X-Ratelimit-Remaining: 0 -> el resto espera al siguiente ciclo
        try:
            send_response(db, rid)
            out["respuestas"] += 1
        except Exception as e:  # noqa: BLE001
            db.rollback()
            log.warning("envio de mensaje receptor %s: %s", rid, type(e).__name__)
    for did in db.scalars(select(EInvoiceDocument.id).where(cond(EInvoiceDocument)).order_by(EInvoiceDocument.id).limit(limit)).all():
        if rate_limited():
            break
        try:
            send_einvoice(db, did)
            out["comprobantes"] += 1
        except Exception as e:  # noqa: BLE001
            db.rollback()
            log.warning("envio de comprobante %s: %s", did, type(e).__name__)
    from .einvoice_delivery import deliver

    # entregas pendientes, en orden: lo que no tiene nada que entregar (respuesta de Hacienda sin XML) o ya se dejo tras
    # el tope de reintentos (delivery_failed_at) no entra; lo que espera su reintento no gasta el cupo del lote, asi
    # que unas pocas direcciones malas no dejan sin turno a los comprobantes nuevos
    pend = db.scalars(
        select(EInvoiceDocument.id)
        .where(
            EInvoiceDocument.mode.in_(("pruebas", "produccion")),
            EInvoiceDocument.xml_document.is_not(None),
            or_(
                EInvoiceDocument.delivered_at.is_(None)
                & EInvoiceDocument.delivery_failed_at.is_(None)
                & EInvoiceDocument.status.in_(("enviado", "procesando", "aceptada", "rechazada")),
                EInvoiceDocument.hacienda_delivered_at.is_(None)
                & EInvoiceDocument.hacienda_delivery_failed_at.is_(None)
                & EInvoiceDocument.xml_response.is_not(None)
                & EInvoiceDocument.status.in_(("aceptada", "rechazada")),
            ),
        )
        .order_by(EInvoiceDocument.id)
        .limit(limit * 10)
    ).all()
    worked = 0
    for did in pend:
        if worked >= limit:
            break
        try:
            n = deliver(db, did, "emitido") + deliver(db, did, "hacienda")
            db.commit()
            out["entregas"] += n
            worked += 1 if n else 0
        except Exception as e:  # noqa: BLE001
            db.rollback()
            worked += 1
            log.warning("entrega del comprobante %s: %s", did, type(e).__name__)
    return out


def poll_status(db: Session, limit: int = 50) -> dict:
    t = now()
    out = {"respuestas": 0, "comprobantes": 0, "recuperados": 0}
    for model, refresh, key in ((ReceptionResponse, refresh_response, "respuestas"), (EInvoiceDocument, refresh_einvoice, "comprobantes")):
        due = db.scalars(
            select(model.id)
            .where(model.status.in_(("enviado", "procesando")), or_(model.next_attempt_at.is_(None), model.next_attempt_at <= t))
            .order_by(model.id)
            .limit(limit)
        ).all()
        stuck = db.scalars(select(model.id).where(model.status == "enviando", model.claimed_at <= t - STUCK).limit(limit)).all()
        for rid in due:
            if rate_limited():
                break
            try:
                refresh(db, rid)
                out[key] += 1
            except Exception as e:  # noqa: BLE001
                db.rollback()
                log.warning("consulta %s %s: %s", key, rid, type(e).__name__)
        for rid in stuck:
            try:
                refresh(db, rid)
                out["recuperados"] += 1
            except Exception as e:  # noqa: BLE001
                db.rollback()
                log.warning("recuperacion %s %s: %s", key, rid, type(e).__name__)
    return out
