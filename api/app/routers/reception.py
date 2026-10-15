"""Recepcion de comprobantes de proveedores: bandeja, detalle, respuesta ante Hacienda (mensaje receptor v4.4),
descargas, cargas manuales, bitacora de correos y buzon. Contabilidad lo opera todo; sin costos ni margen.

Rutas viejas que se conservan: GET /reception (lista), POST /reception/upload (201/409/422), GET /reception/{id}/xml/{which}.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, defer

from ..core.db import get_db
from ..core.deps import Principal, require
from ..core.ratelimit import inbox_run_limiter
from ..models import Media, ReceivedDocument, ReceptionMessage, ReceptionResponse
from ..providers.hacienda import xml44
from ..services import hacienda_config as hc
from ..services import reception as rsvc
from ..services.documents import audit
from ..services.reception_calc import business_days_left

router = APIRouter(prefix="/reception", tags=["recepcion"])

MAX_FILE = 10 * 1024 * 1024
MAX_BATCH = 20
STATUSES = ("por_revisar", "respondido", "sin_respuesta", "ajeno", "invalido", "descartado", "historico")


def _d(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x or 0))


def _crc(doc: ReceivedDocument) -> Decimal:
    return _d(doc.total) * (_d(doc.fx_rate) or 1) if (doc.currency or "CRC") != "CRC" else _d(doc.total)


def _responses_by_doc(db: Session, ids: list[int]) -> dict[int, list[ReceptionResponse]]:
    out: dict[int, list[ReceptionResponse]] = {}
    if not ids:
        return out
    for r in db.scalars(select(ReceptionResponse).where(ReceptionResponse.received_document_id.in_(ids)).order_by(ReceptionResponse.attempt)):
        out.setdefault(r.received_document_id, []).append(r)
    return out


# Listas: los XML completos (comprobante, respuesta del proveedor, mensaje heredado) no se traen; solo si existen.
LIST_OPTIONS = (defer(ReceivedDocument.xml_document), defer(ReceivedDocument.xml_supplier_response), defer(ReceivedDocument.xml_response))
LIST_FLAGS = (ReceivedDocument.xml_supplier_response.is_not(None).label("has_sr"), ReceivedDocument.xml_response.is_not(None).label("has_xr"))


def _list_rows(db: Session, stmt) -> list[tuple[ReceivedDocument, tuple[bool, bool]]]:
    return [(d, (bool(a), bool(b))) for d, a, b in db.execute(stmt.add_columns(*LIST_FLAGS).options(*LIST_OPTIONS)).all()]


def item(doc: ReceivedDocument, rs: list[ReceptionResponse] | None = None, today: date | None = None, flags: tuple[bool, bool] | None = None) -> dict:
    """flags: (tiene respuesta del proveedor, tiene mensaje heredado) ya calculados en SQL por las listas."""
    rs = rs or []
    last = rs[-1] if rs else None
    has_sr, has_xr = flags if flags is not None else (bool(doc.xml_supplier_response), bool(doc.xml_response))
    return {
        "id": doc.id,
        "clave": doc.clave,
        "consecutive": doc.consecutive,
        "doc_type": doc.doc_type,
        "issuer_name": doc.issuer_name,
        "issuer_id": doc.issuer_id,
        "issuer_email": doc.issuer_email,
        "issue_date": doc.issue_date,
        "currency": doc.currency,
        "fx_rate": doc.fx_rate,
        "subtotal": doc.subtotal,
        "tax_total": doc.tax_total,
        "iva_total": doc.iva_total if doc.iva_total is not None else doc.tax_total,
        "other_charges": doc.other_charges,
        "total": doc.total,
        "total_crc": _crc(doc),
        "status": doc.status,
        "action": doc.action,
        "iva_condition": doc.iva_condition if doc.action else None,
        "activity_code": doc.activity_code,
        "issuer_activity_code": doc.issuer_activity_code,
        "credit_amount": doc.credit_amount,
        "applicable_amount": doc.applicable_amount,
        "hacienda_status": doc.hacienda_status,
        "supplier_hacienda_status": doc.supplier_hacienda_status,
        "deadline": doc.deadline,
        "business_days_left": business_days_left(today or date.today(), doc.deadline),
        "has_pdf": bool(doc.pdf_media_id),
        "has_supplier_response": has_sr,
        "has_response": has_xr or any(r.xml_signed for r in rs),
        "has_hacienda_answer": bool(last and last.hacienda_xml),
        "expense_id": doc.expense_id,
        "receiver_ok": doc.receiver_ok,
        "origin": doc.origin,
        "created_at": doc.created_at,
    }


def _response_out(r: ReceptionResponse) -> dict:
    from ..services.reception_calc import IVA_NAMES

    return {
        "id": r.id,
        "attempt": r.attempt,
        "message_code": r.message_code,
        "iva_condition": IVA_NAMES.get(r.iva_condition_code or "", None),
        "activity_code": r.activity_code,
        "credit_amount": r.credit_amount,
        "applicable_amount": r.applicable_amount,
        "detail": r.detail,
        "consecutive": r.consecutive,
        "mode": r.mode,
        "status": r.status,
        "hacienda_detail": r.hacienda_detail,
        "sent_at": r.sent_at,
        "resolved_at": r.resolved_at,
        "attempts": r.attempts,
        "last_error": r.last_error,
        "emitted_at": r.emitted_at,
        "created_by": r.created_by,
        "created_at": r.created_at,
    }


def detail(db: Session, p: Principal, doc: ReceivedDocument, extra_warnings: list[str] | None = None) -> dict:
    rs = rsvc.responses(db, doc)
    out = item(doc, rs)
    lines, warnings = [], list(extra_warnings or [])
    if doc.xml_document:
        try:
            parsed = xml44.parse_comprobante(xml44.parse_xml_bytes(doc.xml_document.encode("utf-8")))
            lines = parsed.lines
            warnings += parsed.warnings
        except Exception:  # noqa: BLE001
            warnings.append("No se pudo leer el detalle del XML")
    if doc.status in ("por_revisar",) and not doc.xml_supplier_response:
        warnings.append("El proveedor no adjuntó la respuesta de Hacienda")
    if doc.supplier_hacienda_status == "rechazado" and doc.status in ("respondido", "historico"):
        warnings.append("Hacienda rechazó este comprobante al proveedor: no da crédito")
    if doc.status == "por_revisar" and rsvc.overdue(doc):
        warnings.append("Fuera de plazo: Hacienda presume la aceptación total y puede rechazar el mensaje")
    if not p.tenant.tax_id and doc.status == "por_revisar":
        warnings.append("Configure la cédula en Ajustes → Hacienda")
    nc_ref = rsvc.nc_adjustment(db, doc)
    proposal = None
    if nc_ref is not None:
        what = "rechazaste" if nc_ref.action == "rechazada" else "aceptaste parcialmente"
        warnings.append(
            f"Esta nota de crédito corrige una factura que {what}: el gasto y el crédito ya quedaron por el monto aceptado"
        )
        proposal = {"action": "aceptada", "iva_condition": "no_credito", "create_expense": False, "reference_id": nc_ref.id}
    msg = db.get(ReceptionMessage, doc.message_id) if doc.message_id else None
    mode = hc.reception_mode(db, p.tenant)
    last = rs[-1] if rs else None
    can_reopen = rsvc.reopen_blocker(db, p.tenant, doc, rs) is None
    out.update(
        {
            "lines": lines,
            "reference_clave": doc.reference_clave,
            "schema_version": doc.schema_version,
            "receiver_id": doc.receiver_id,
            "message": {"id": msg.id, "sender": msg.sender, "subject": msg.subject, "received_at": msg.received_at} if msg else None,
            "responses": [_response_out(r) for r in rs],
            "discarded_reason": doc.discarded_reason,
            "response_detail": doc.response_detail,
            "mode": mode,
            "can_respond": rsvc.can_respond(db, doc) and doc.doc_type != "TE",
            "can_retry": bool(last and last.status in ("rechazado", "error") and doc.status == "respondido"),
            "can_reopen": can_reopen,
            "can_send": bool(last and last.status == "por_enviar"),
            "can_refresh": bool(last and last.status in ("enviado", "procesando", "enviando")),
            "can_expense": not doc.expense_id and (doc.doc_type == "TE" or (doc.status in ("respondido", "historico") and doc.action in ("aceptada", "parcial")) or (doc.status == "ajeno" and not doc.receiver_id)),
            "warnings": list(dict.fromkeys(warnings)),
            "expense_candidates": rsvc.expense_candidates(db, doc)
            if doc.status == "por_revisar" or (doc.status in ("respondido", "historico") and doc.action in ("aceptada", "parcial") and not doc.expense_id)
            else [],
            "proposal": proposal,
            "supplier_hacienda_detail": doc.supplier_hacienda_detail,
        }
    )
    return out


@router.get("")
def inbox(p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    rows = _list_rows(db, select(ReceivedDocument).where(ReceivedDocument.tenant_id == p.tenant.id).order_by(ReceivedDocument.id.desc()).limit(100))
    rs = _responses_by_doc(db, [d.id for d, _ in rows])
    return [item(d, rs.get(d.id), flags=f) for d, f in rows]


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@router.get("/documents")
def documents(
    status: str | None = None,
    doc_type: str | None = None,
    q: str | None = None,
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    due: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    p: Principal = Depends(require("accounting", "ver")),
    db: Session = Depends(get_db),
):
    base = select(ReceivedDocument).where(ReceivedDocument.tenant_id == p.tenant.id)
    if doc_type:
        base = base.where(ReceivedDocument.doc_type == doc_type)
    if from_:
        base = base.where(ReceivedDocument.issue_date >= from_)
    if to:
        base = base.where(ReceivedDocument.issue_date <= to)
    if q and q.strip():
        like = _like(q.strip())
        base = base.where(
            or_(
                ReceivedDocument.issuer_name.ilike(like, escape="\\"),
                ReceivedDocument.issuer_id.ilike(like, escape="\\"),
                ReceivedDocument.consecutive.ilike(like, escape="\\"),
                ReceivedDocument.clave.ilike(like, escape="\\"),
            )
        )
    sub = base.subquery()
    counts = dict(db.execute(select(sub.c.status, func.count()).group_by(sub.c.status)).all())
    query = base
    if status:
        query = query.where(ReceivedDocument.status == status)
    if due == "pronto":
        from datetime import timedelta

        limite = date.today() + timedelta(days=4)  # filtro grueso; el fino por dias habiles va abajo
        query = query.where(ReceivedDocument.status == "por_revisar", ReceivedDocument.deadline.is_not(None), ReceivedDocument.deadline <= limite)
    if status == "por_revisar" or due == "pronto":
        query = query.order_by(ReceivedDocument.deadline.asc().nulls_last(), ReceivedDocument.id)
    else:
        query = query.order_by(ReceivedDocument.issue_date.desc().nulls_last(), ReceivedDocument.id.desc())
    if due == "pronto":
        # el filtro grueso ya lo acota a lo por revisar que vence en <= 4 dias: el fino (dias habiles) va en Python
        rows = [x for x in _list_rows(db, query) if (business_days_left(date.today(), x[0].deadline) or 0) <= 2]
        total, page = len(rows), rows[offset : offset + limit]
    else:
        # conteo y pagina en SQL: con el historico de Fygaro son miles de filas (y nunca se traen sus XML)
        total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
        page = _list_rows(db, query.offset(offset).limit(limit))
    rs = _responses_by_doc(db, [d.id for d, _ in page])
    return {"items": [item(d, rs.get(d.id), flags=f) for d, f in page], "total": total, "counts": {s: counts.get(s, 0) for s in STATUSES}}


@router.get("/summary")
def summary(p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    today = date.today()
    pend = db.scalars(select(ReceivedDocument).where(ReceivedDocument.tenant_id == p.tenant.id, ReceivedDocument.status == "por_revisar")).all()
    left = [business_days_left(today, d.deadline) for d in pend]
    last_ids = select(func.max(ReceptionResponse.id)).where(ReceptionResponse.tenant_id == p.tenant.id).group_by(ReceptionResponse.received_document_id)
    last = db.scalars(select(ReceptionResponse).where(ReceptionResponse.id.in_(last_ids))).all()
    return {
        "por_revisar": len(pend),
        "vencen_pronto": sum(1 for x in left if x is not None and 0 <= x <= 2),
        "vencidos": sum(1 for x in left if x is not None and x < 0),
        "mensajes_rechazados": sum(1 for r in last if r.status == "rechazado"),
        "errores": sum(1 for r in last if r.status == "error"),
    }


# ---------------------------------------------------------------- correos y buzon
def _message_out(m: ReceptionMessage) -> dict:
    return {
        "id": m.id,
        "source": m.source,
        "sender": m.sender,
        "subject": m.subject,
        "received_at": m.received_at,
        "size": m.size,
        "status": m.status,
        "summary": m.summary or [],
        "error": m.error,
        "created_at": m.created_at,
        "can_reprocess": m.status in ("error", "parcial") and m.source == "imap",
    }


@router.get("/messages")
def messages(
    all: int = 0,  # noqa: A002 - ?all=1 tambien muestra los correos sin comprobantes
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    p: Principal = Depends(require("accounting", "ver")),
    db: Session = Depends(get_db),
):
    q = select(ReceptionMessage).where(ReceptionMessage.tenant_id == p.tenant.id)
    if not all:
        q = q.where(ReceptionMessage.status != "sin_comprobantes")
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(ReceptionMessage.id.desc()).offset(offset).limit(limit)).all()
    return {"items": [_message_out(m) for m in rows], "total": total}


@router.get("/inbox/status")
def inbox_status(p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    from ..services.inbox import status

    return status(db, p.tenant)


@router.post("/inbox/run")
def inbox_run(p: Principal = Depends(require("accounting", "crear")), db: Session = Depends(get_db)):
    from ..services.inbox import poll_http

    inbox_run_limiter.hit(str(p.tenant.id))
    return poll_http(db, p.tenant, "Buzón apagado: configúrelo en Ajustes → XML de proveedores o en las variables del servidor")


@router.post("/messages/{mid}/reprocess")
def reprocess(mid: int, p: Principal = Depends(require("accounting", "crear")), db: Session = Depends(get_db)):
    from ..services.inbox import process_raw

    m = db.get(ReceptionMessage, mid)
    if not m or m.tenant_id != p.tenant.id:
        raise HTTPException(404, "Correo no encontrado")
    if m.status not in ("error", "parcial") or not m.raw_eml:
        raise HTTPException(409, "Solo se reprocesa un correo con error que guardó su .eml")
    _, res = process_raw(db, p.tenant, m.raw_eml, mailbox_id=m.mailbox_id, uid=m.uid, uidvalidity=m.uidvalidity, user_id=p.user.id, message=m)
    audit(db, p.tenant.id, p.user.id, "reception_reprocess", "reception_message", m.id, {"estado": m.status}, ip=p.ip)
    db.commit()
    return {**{k: v for k, v in res.items() if k != "summary"}, "message": _message_out(m)}


class AssignPdfIn(BaseModel):
    media_id: int
    received_document_id: int


@router.post("/messages/{mid}/assign-pdf")
def assign_pdf(mid: int, data: AssignPdfIn, p: Principal = Depends(require("accounting", "crear")), db: Session = Depends(get_db)):
    from ..services import media_access as ma

    m = db.get(ReceptionMessage, mid)
    if not m or m.tenant_id != p.tenant.id:
        raise HTTPException(404, "Correo no encontrado")
    # solo un PDF "sin asignar" de ESTE correo (privado de contabilidad): nunca cualquier Media por id
    pending = {x.get("media_id") for x in (m.summary or []) if x.get("result") == "sin_asignar"}
    media = db.get(Media, data.media_id) if data.media_id in pending else None
    if (
        not media
        or media.tenant_id != p.tenant.id
        or media.content_type != "application/pdf"
        or media.visibility != "private"
        or media.module != "accounting"
        or not ma.can_view(p, media)
    ):
        raise HTTPException(404, "PDF no encontrado")
    doc = rsvc.get_doc(db, p.tenant.id, data.received_document_id)
    doc.pdf_media_id = media.id
    summ = [dict(x) for x in (m.summary or [])]
    for x in summ:
        if x.get("media_id") == media.id:
            x.update(result="pdf", received_document_id=doc.id, clave=doc.clave)
    m.summary = summ
    audit(db, p.tenant.id, p.user.id, "reception_assign_pdf", "received_document", doc.id, {"media_id": media.id}, ip=p.ip)
    db.commit()
    return _message_out(m)


# ---------------------------------------------------------------- cargas manuales
async def _read(f: UploadFile) -> bytes:
    data = await f.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise HTTPException(413, f"{f.filename or 'Archivo'}: muy grande (máximo 10 MB)")
    return data


@router.post("/upload", status_code=201)
async def upload(file: UploadFile = File(...), p: Principal = Depends(require("accounting", "crear")), db: Session = Depends(get_db)):
    from ..services.reception_intake import ingest_files, sniff

    data = await _read(file)
    name = file.filename or "archivo"
    kind = sniff(data)
    if kind == "xml":
        try:
            root = xml44.parse_xml_bytes(data)
        except xml44.XmlRejected as e:
            raise HTTPException(422, str(e)) from e
        cls = xml44.classify(root)
        if cls == "otro":
            raise HTTPException(422, "El XML no es un comprobante electrónico ni una respuesta de Hacienda")
        if cls == "comprobante":
            res = ingest_files(db, p.tenant, [(name, data)], source="manual", message=None, user_id=p.user.id)
            if res["errores"]:
                db.rollback()
                raise HTTPException(422, res["errores"][0]["error"])
            if res["duplicados"]:
                db.rollback()
                raise HTTPException(409, "Ese comprobante ya fue recibido")
            if not res["nuevos"]:
                db.rollback()
                raise HTTPException(422, (res["ignorados"][0]["reason"] if res["ignorados"] else "No se registró el comprobante"))
            db.commit()
            doc = db.get(ReceivedDocument, res["nuevos"][0])
            return item(doc)
        res = ingest_files(db, p.tenant, [(name, data)], source="manual", message=None, user_id=p.user.id)
        if not res["adjuntos"]:
            db.rollback()
            reason = res["ignorados"][0]["reason"] if res["ignorados"] else (res["errores"][0]["error"] if res["errores"] else "No se pudo usar el archivo")
            raise HTTPException(422, reason)
        db.commit()
        return JSONResponse(jsonable_encoder({k: v for k, v in res.items() if k != "summary"}), status_code=200)
    if kind in ("pdf", "zip"):
        res = ingest_files(db, p.tenant, [(name, data)], source="manual", message=None, user_id=p.user.id)
        db.commit()
        return JSONResponse(jsonable_encoder({k: v for k, v in res.items() if k != "summary"}), status_code=200)
    raise HTTPException(422, "Formato no reconocido: suba XML, PDF o zip")


@router.post("/upload-batch")
async def upload_batch(files: list[UploadFile] = File(...), p: Principal = Depends(require("accounting", "crear")), db: Session = Depends(get_db)):
    from ..services.reception_intake import ingest_files, status_of

    if len(files) > MAX_BATCH:
        raise HTTPException(422, f"Máximo {MAX_BATCH} archivos por carga")
    pairs = [(f.filename or "archivo", await _read(f)) for f in files]
    h = hashlib.sha256()
    for n, b in pairs:
        h.update(n.encode("utf-8", "ignore") + b"\0" + b)
    m = ReceptionMessage(
        tenant_id=p.tenant.id,
        source="manual",
        sender=(p.user.email or "")[:300],
        subject=f"Carga manual ({len(pairs)} archivo{'s' if len(pairs) != 1 else ''})",
        received_at=datetime.now(UTC),
        size=sum(len(b) for _, b in pairs),
        sha256=h.hexdigest(),
        created_by=p.user.id,
        status="procesado",
    )
    db.add(m)
    db.flush()
    res = ingest_files(db, p.tenant, pairs, source="manual", message=m, user_id=p.user.id)
    m.status = status_of(res)
    m.error = "; ".join(e["error"] for e in res["errores"])[:500] or None
    audit(db, p.tenant.id, p.user.id, "reception_upload", "reception_message", m.id, {"archivos": len(pairs), "nuevos": len(res["nuevos"])}, ip=p.ip)
    db.commit()
    return {**res, "message_id": m.id}


# ---------------------------------------------------------------- un comprobante
@router.get("/{rid}")
def get_one(rid: int, p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    return detail(db, p, rsvc.get_doc(db, p.tenant.id, rid))


FILE_NAMES = {
    "document": "{clave}.xml",
    "supplier_response": "{clave}-respuesta-proveedor.xml",
    "mr": "{clave}-mensaje-receptor.xml",
    "hacienda": "{clave}-respuesta-hacienda.xml",
    "pdf": "{clave}.pdf",
}


def _file(db: Session, doc: ReceivedDocument, which: str) -> tuple[bytes, str] | None:
    last = next((r for r in reversed(rsvc.responses(db, doc)) if r.xml_signed), None)
    last_any = rsvc.last_response(db, doc)
    if which == "document":
        body = doc.xml_document
    elif which == "supplier_response":
        body = doc.xml_supplier_response
    elif which == "mr":
        body = last.xml_signed if last else doc.xml_response
    elif which == "hacienda":
        body = last_any.hacienda_xml if last_any and last_any.hacienda_xml else (last.hacienda_xml if last else None)
    elif which == "pdf":
        if not doc.pdf_media_id:
            return None
        m = db.get(Media, doc.pdf_media_id)
        return (bytes(m.data), "application/pdf") if m and m.data else None
    else:
        return None
    return (body.encode("utf-8"), "application/xml") if body else None


@router.get("/{rid}/file/{which}")
def download(rid: int, which: str, p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    doc = rsvc.get_doc(db, p.tenant.id, rid)
    if which == "todo":
        buf = io.BytesIO()
        n = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for w, pattern in FILE_NAMES.items():
                f = _file(db, doc, w)
                if f:
                    z.writestr(pattern.format(clave=doc.clave), f[0])
                    n += 1
        if not n:
            raise HTTPException(404, "No disponible")
        return Response(buf.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{doc.clave}.zip"'})
    if which not in FILE_NAMES:
        raise HTTPException(404, "No disponible")
    f = _file(db, doc, which)
    if not f:
        raise HTTPException(404, "No disponible")
    return Response(f[0], media_type=f[1], headers={"Content-Disposition": f'attachment; filename="{FILE_NAMES[which].format(clave=doc.clave)}"'})


@router.get("/{rid}/xml/{which}")
def xml(rid: int, which: str, p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    """Alias viejo: document | response (= mensaje receptor)."""
    doc = rsvc.get_doc(db, p.tenant.id, rid)
    f = _file(db, doc, "document" if which == "document" else "mr")
    if not f:
        raise HTTPException(404, "XML no disponible")
    return Response(f[0], media_type="application/xml", headers={"Content-Disposition": f'attachment; filename="{doc.clave}-{which}.xml"'})


@router.post("/{rid}/respond")
def respond(rid: int, data: rsvc.RespondIn, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    doc, _r, warnings = rsvc.respond(db, p, rid, data)
    return detail(db, p, doc, warnings)


@router.post("/{rid}/send")
def send_now(rid: int, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    from ..services.hacienda_sync import send_response

    doc = rsvc.get_doc(db, p.tenant.id, rid)
    last = rsvc.last_response(db, doc)
    if not last or last.status != "por_enviar":
        raise HTTPException(409, "No hay un mensaje por enviar")
    if last.next_attempt_at is not None:
        last.next_attempt_at = None  # "Enviar ahora" no espera el reintento programado
        db.commit()
    send_response(db, last.id)
    db.refresh(doc)
    return detail(db, p, doc)


@router.post("/{rid}/refresh")
def refresh(rid: int, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    from ..services.hacienda_sync import refresh_response

    doc = rsvc.get_doc(db, p.tenant.id, rid)
    last = rsvc.last_response(db, doc)
    if not last or last.status not in ("enviado", "procesando", "enviando"):
        raise HTTPException(409, "No hay un mensaje enviado por consultar")
    refresh_response(db, last.id)
    db.refresh(doc)
    return detail(db, p, doc)


@router.post("/{rid}/retry")
def retry(rid: int, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    doc = rsvc.get_doc(db, p.tenant.id, rid, lock=True)
    rsvc.retry(db, p, doc)
    db.commit()
    return detail(db, p, doc)


class ReopenIn(BaseModel):
    confirm_not_in_fygaro: bool = False


@router.post("/{rid}/reopen")
def reopen(rid: int, data: ReopenIn, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    doc = rsvc.get_doc(db, p.tenant.id, rid, lock=True)
    rsvc.reopen(db, p, doc, data.confirm_not_in_fygaro)
    db.commit()
    return detail(db, p, doc)


class DiscardIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


@router.post("/{rid}/discard")
def discard(rid: int, data: DiscardIn, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    doc = rsvc.get_doc(db, p.tenant.id, rid, lock=True)
    rsvc.discard(db, p, doc, data.reason)
    db.commit()
    return detail(db, p, doc)


@router.post("/{rid}/restore")
def restore(rid: int, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    doc = rsvc.get_doc(db, p.tenant.id, rid, lock=True)
    rsvc.restore(db, p, doc)
    db.commit()
    return detail(db, p, doc)


class ExpenseFromDocIn(BaseModel):
    category_id: int | None = None


@router.post("/{rid}/expense", status_code=201)
def expense(rid: int, data: ExpenseFromDocIn | None = None, p: Principal = Depends(require("accounting", "crear")), db: Session = Depends(get_db)):
    doc = rsvc.get_doc(db, p.tenant.id, rid, lock=True)
    e = rsvc.expense_for_document(db, p, doc, data.category_id if data else None)
    db.commit()
    return {"expense_id": e.id, "document": detail(db, p, doc)}


class LinkExpenseIn(BaseModel):
    expense_id: int


@router.post("/{rid}/link-expense")
def link_expense(rid: int, data: LinkExpenseIn, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    doc = rsvc.get_doc(db, p.tenant.id, rid, lock=True)
    if doc.status not in ("respondido", "historico") or doc.action not in ("aceptada", "parcial"):
        raise HTTPException(409, "Solo se vincula un gasto a un comprobante aceptado")
    if doc.expense_id:
        raise HTTPException(409, "El comprobante ya tiene un gasto ligado")
    rsvc.link_expense(db, p, doc, data.expense_id, rsvc.amounts_from_response(doc))
    db.commit()
    return detail(db, p, doc)


@router.post("/{rid}/attach")
async def attach(rid: int, file: UploadFile = File(...), p: Principal = Depends(require("accounting", "crear")), db: Session = Depends(get_db)):
    from ..routers.media import save_media
    from ..services.reception_intake import apply_supplier_response, sniff, supplier_response_problem

    doc = rsvc.get_doc(db, p.tenant.id, rid)
    data = await _read(file)
    kind = sniff(data)
    if kind == "pdf":
        m, _ = save_media(db, p.tenant.id, data, file.filename, p.user.id, ip=p.ip, visibility="private", module="accounting")
        doc.pdf_media_id = m.id
    elif kind == "xml":
        try:
            root = xml44.parse_xml_bytes(data)
        except xml44.XmlRejected as e:
            raise HTTPException(422, str(e)) from e
        if xml44.classify(root) != "mensaje_hacienda":
            raise HTTPException(422, "Suba el PDF o la respuesta de Hacienda (MensajeHacienda) de este comprobante")
        if xml44.text(root, "Clave") != doc.clave:
            raise HTTPException(422, "La respuesta de Hacienda es de otra clave")
        problem = supplier_response_problem(doc, root)
        if problem:
            raise HTTPException(422, problem[:1].upper() + problem[1:])
        antes = (doc.status, doc.supplier_hacienda_status)
        # reemplaza la que hubiera (p. ej. una llegada por correo) y recalcula el estado: invalido <-> por_revisar
        doc.xml_supplier_response = None
        apply_supplier_response(db, doc, root, xml44.to_utf8_text(data, root))
        if antes != (doc.status, doc.supplier_hacienda_status):
            audit(db, p.tenant.id, p.user.id, "supplier_response_replaced", "received_document", doc.id, {"antes": list(antes), "despues": [doc.status, doc.supplier_hacienda_status]}, ip=p.ip)
    else:
        raise HTTPException(422, "Suba un PDF o un XML de respuesta de Hacienda")
    audit(db, p.tenant.id, p.user.id, "reception_attach", "received_document", doc.id, {"tipo": kind}, ip=p.ip)
    db.commit()
    return detail(db, p, doc)
