"""Entrada de comprobantes recibidos (buzon, carga manual): clasifica por contenido, registra, empareja respuestas de
Hacienda y PDF por clave/consecutivo, y guarda los mensajes receptor que NUESTROS clientes mandan sobre lo que emitimos.

El correo es no confiable: nunca se acepta nada solo (la decision es de contabilidad); no se siguen enlaces.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Customer, EInvoiceDocument, Invoice, ReceivedDocument, ReceptionMessage, Tenant
from ..providers.hacienda import xml44
from .documents import audit
from .reception_calc import deadline

ZIP_MAX_ENTRIES = 200
ZIP_MAX_ENTRY = 10 * 1024 * 1024
ZIP_MAX_TOTAL = 50 * 1024 * 1024
ZIP_MAX_RATIO = 100
# Tope de lo descomprimido entre TODOS los zip de un correo o lote (ZIP_MAX_TOTAL es por zip): sin el, un correo de 25 MB
# con decenas de zips chicos de relacion ~100 expandia GB en memoria y el contenedor moria por falta de memoria.
INTAKE_MAX_EXPANDED = 50 * 1024 * 1024
ZIP_ERROR = "zip demasiado grande o sospechoso"
CLAVE_RE = re.compile(r"(?<!\d)(\d{50})(?!\d)")
CONSEC_RE = re.compile(r"(?<!\d)(\d{20})(?!\d)")


class ZipRejected(Exception):
    pass


def sniff(data: bytes) -> str:
    head = xml44.clean_bytes(data[:64])
    if head.startswith(b"%PDF-"):
        return "pdf"
    if data[:4] == b"PK\x03\x04":
        return "zip"
    if head.startswith(b"<") or data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "xml"
    return "otro"


def _safe_name(name: str) -> bool:
    p = PurePosixPath(name.replace("\\", "/"))
    return not (p.is_absolute() or ".." in p.parts or re.match(r"^[A-Za-z]:", name))


def expand_zip(data: bytes, depth: int = 0, *, max_total: int = ZIP_MAX_TOTAL) -> list[tuple[str, bytes]]:
    """Entradas de un zip con limites (200 entradas, 10 MB c/u, 50 MB en total, relacion <= 100, sin rutas raras,
    un nivel de zip anidado como maximo). Cualquier exceso -> ZipRejected."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ZipRejected("zip dañado") from e
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > ZIP_MAX_ENTRIES:
        raise ZipRejected(ZIP_ERROR)
    out: list[tuple[str, bytes]] = []
    total = 0
    for info in infos:
        if not _safe_name(info.filename):
            raise ZipRejected(ZIP_ERROR)
        if info.file_size > ZIP_MAX_ENTRY or (info.compress_size and info.file_size / max(info.compress_size, 1) > ZIP_MAX_RATIO):
            raise ZipRejected(ZIP_ERROR)
        with zf.open(info) as fh:
            content = fh.read(ZIP_MAX_ENTRY + 1)
        if len(content) > ZIP_MAX_ENTRY:
            raise ZipRejected(ZIP_ERROR)
        total += len(content)
        if total > max_total:
            raise ZipRejected(ZIP_ERROR)
        name = PurePosixPath(info.filename.replace("\\", "/")).name
        if sniff(content) == "zip":
            if depth >= 1:
                raise ZipRejected(ZIP_ERROR)
            inner = expand_zip(content, depth + 1, max_total=max_total - total)
            total += sum(len(b) for _, b in inner)
            if total > max_total:
                raise ZipRejected(ZIP_ERROR)
            out += inner
        else:
            out.append((name, content))
    return out


@dataclass
class Item:
    name: str
    data: bytes
    kind: str  # comprobante | mensaje_hacienda | mensaje_receptor | pdf | otro
    root: object = None
    error: str | None = None


def new_result() -> dict:
    return {"nuevos": [], "duplicados": [], "adjuntos": [], "ignorados": [], "errores": [], "sin_asignar": [], "summary": []}


def company_id(tenant: Tenant) -> str | None:
    return xml44.norm_id(tenant.tax_id)


def initial_status(tenant: Tenant, p: xml44.ParsedDoc) -> tuple[str | None, str | None]:
    """(estado, motivo). Estado None = no se registra (lo emitio la empresa)."""
    me = company_id(tenant)
    if me and xml44.same_id(p.issuer_id, me):
        return None, "es un comprobante emitido por la empresa"
    if p.doc_type in ("FEC", "FEE", "REP"):
        return "sin_respuesta", "comprobante informativo: no lleva mensaje receptor"
    if p.doc_type == "TE":
        return "sin_respuesta", "el tiquete no lleva mensaje receptor ni da crédito"
    if p.receiver_id and me and not xml44.same_id(p.receiver_id, me):
        return "ajeno", "el receptor no es la empresa"
    if not p.receiver_id:
        return "ajeno", "sin receptor identificado: Hacienda rechazaría el mensaje porque la cédula del receptor debe coincidir"
    return "por_revisar", None if me else "Configure la cédula en Ajustes → Hacienda"


def register_document(
    db: Session,
    tenant: Tenant,
    parsed: xml44.ParsedDoc,
    raw_text: str,
    *,
    origin: str,
    message: ReceptionMessage | None,
    status_override: str | None = None,
) -> tuple[str, ReceivedDocument | None, str | None]:
    """("nuevo" | "duplicado" | "ignorado", documento, motivo)."""
    existing = db.scalar(select(ReceivedDocument).where(ReceivedDocument.tenant_id == tenant.id, ReceivedDocument.clave == parsed.clave))
    if existing:
        return "duplicado", existing, None
    status, reason = initial_status(tenant, parsed)
    if status is None:
        return "ignorado", None, reason
    me = company_id(tenant)
    doc = ReceivedDocument(
        tenant_id=tenant.id,
        clave=parsed.clave,
        consecutive=parsed.consecutive,
        issuer_name=parsed.issuer_name,
        issuer_id=parsed.issuer_id,
        issuer_id_type=parsed.issuer_id_type,
        issuer_email=parsed.issuer_email,
        issuer_activity_code=(parsed.issuer_activity_code or "")[:10] or None,
        issue_date=parsed.issue_date,
        currency=parsed.currency,
        fx_rate=parsed.fx_rate,
        subtotal=parsed.net,
        tax_total=parsed.tax_total,
        iva_total=parsed.iva_total,
        other_charges=parsed.other_charges,
        total=parsed.total,
        doc_type=parsed.doc_type,
        schema_version=parsed.schema_version,
        receiver_id=(parsed.receiver_id or "")[:20] or None,
        receiver_ok=xml44.same_id(parsed.receiver_id, me) if parsed.receiver_id and me else None,
        reference_clave=parsed.reference_clave,
        status=status_override or status,
        origin=origin,
        message_id=message.id if message else None,
        xml_document=raw_text,
        hacienda_status="sin_respuesta",
        iva_condition="credito",
        deadline=deadline(parsed.issue_date),
    )
    sp = db.begin_nested()
    try:
        db.add(doc)
        db.flush()
        sp.commit()
    except IntegrityError:
        sp.rollback()  # carrera entre el worker y una carga manual: el unico (tenant, clave) decide
        existing = db.scalar(select(ReceivedDocument).where(ReceivedDocument.tenant_id == tenant.id, ReceivedDocument.clave == parsed.clave))
        return "duplicado", existing, None
    return "nuevo", doc, reason


def _classify(name: str, data: bytes) -> Item:
    kind = sniff(data)
    if kind == "pdf":
        return Item(name, data, "pdf")
    if kind == "xml":
        try:
            root = xml44.parse_xml_bytes(data)
        except xml44.XmlRejected as e:
            return Item(name, data, "otro", error=str(e))
        return Item(name, data, xml44.classify(root), root=root)
    return Item(name, data, kind)


SUPPLIER_MISMATCH = "la respuesta de Hacienda no es de este comprobante: las cédulas del emisor o del receptor no coinciden"


def supplier_response_problem(doc: ReceivedDocument, root) -> str | None:
    """El MensajeHacienda llega por correo sin autenticar y su firma no se verifica (fase 1): ademas de la clave, el
    emisor tiene que ser el del comprobante y el receptor (si el comprobante lo trae) el mismo. None = coincide."""
    mh = xml44.parse_mensaje_hacienda(root)
    if not xml44.same_id(mh["issuer_id"], doc.issuer_id):
        return SUPPLIER_MISMATCH
    if doc.receiver_id and not xml44.same_id(mh["receiver_id"], doc.receiver_id):
        return SUPPLIER_MISMATCH
    return None


def _status_from_supplier(doc: ReceivedDocument) -> None:
    """por_revisar <-> invalido segun la respuesta al proveedor (§6.3.1). Reversible: si se adjunta a mano la respuesta
    correcta (aceptada) de un comprobante que quedo invalido por otra, vuelve a por_revisar."""
    rejected = doc.supplier_hacienda_status == "rechazado"
    if doc.status == "descartado":
        if rejected and doc.status_before_discard == "por_revisar":
            doc.status_before_discard = "invalido"
        elif not rejected and doc.status_before_discard == "invalido":
            doc.status_before_discard = "por_revisar"
    elif rejected and doc.status == "por_revisar":
        doc.status = "invalido"
    elif not rejected and doc.status == "invalido":
        doc.status = "por_revisar"


def apply_supplier_response(db: Session, doc: ReceivedDocument, root, raw_text: str) -> bool:
    """Guarda la respuesta de Hacienda al proveedor. Devuelve False si ya tenia una o si no es de este comprobante
    (supplier_response_problem)."""
    if doc.xml_supplier_response or supplier_response_problem(doc, root):
        return False
    mh = xml44.parse_mensaje_hacienda(root)
    doc.xml_supplier_response = raw_text
    doc.supplier_hacienda_status = {"1": "aceptado", "3": "rechazado"}.get(mh["mensaje"] or "", "desconocido")
    doc.supplier_hacienda_detail = mh["detalle"]
    _status_from_supplier(doc)
    return True


def _our_receiver(db: Session, doc: EInvoiceDocument) -> str | None:
    """Cedula del receptor de NUESTRO comprobante: la del XML firmado; si no hay, la del cliente de la factura."""
    if doc.xml_document:
        try:
            n = xml44.text(xml44.parse_xml_bytes(doc.xml_document.encode("utf-8")), "Receptor", "Identificacion", "Numero")
            if n:
                return n
        except Exception:  # noqa: BLE001 - XML propio ilegible: se usa el cliente
            pass
    inv = db.get(Invoice, doc.invoice_id)
    c = db.get(Customer, inv.customer_id) if inv and inv.customer_id else None
    if c is not None and c.tenant_id != doc.tenant_id:
        return None  # un cliente de otra empresa no cuenta como nuestro receptor
    return c.id_number if c and c.id_number else None


def apply_client_receptor_message(db: Session, tenant: Tenant, root, raw_text: str) -> tuple[EInvoiceDocument | None, str | None]:
    """Mensaje receptor de NUESTRO cliente sobre un comprobante emitido por la empresa (Res. 0027-2024 art. 10): se liga
    a la factura y, con aceptacion parcial o rechazo, alerta para emitir la nota de credito.

    Llega por correo sin autenticar y la firma no se verifica: solo cuenta si la cedula del receptor es la del cliente
    de esa factura, y el primero que llega no se sobrescribe (Hacienda solo admite un mensaje por clave). Devuelve
    (documento, None) o (None, motivo para ignorarlo)."""
    tercero = "mensaje de aceptación de un tercero"
    mr = xml44.parse_mensaje_receptor(root)
    if not mr["clave"] or not xml44.same_id(mr["issuer_id"], tenant.tax_id):
        return None, tercero
    doc = db.scalar(select(EInvoiceDocument).where(EInvoiceDocument.tenant_id == tenant.id, EInvoiceDocument.clave == mr["clave"]))
    if doc is None:
        return None, tercero
    msg = (mr["mensaje"] or "")[:1] or None
    ours = _our_receiver(db, doc)
    if not ours or not mr["receiver_id"] or not xml44.same_id(mr["receiver_id"], ours):
        why = {"clave": doc.clave, "mensaje": msg, "motivo": "la cédula del receptor no es la del cliente"}
        audit(db, tenant.id, None, "client_receptor_message_ignored", "invoice", doc.invoice_id, why)
        return None, f"{tercero}: la cédula del receptor no es la del cliente de la factura"
    if doc.receiver_message:
        if doc.receiver_message == msg and (doc.receiver_detail or None) == mr["detalle"]:
            return doc, None  # el mismo mensaje otra vez (reenvio): sin cambios ni alerta
        audit(
            db, tenant.id, None, "client_receptor_message_ignored", "invoice", doc.invoice_id,
            {"clave": doc.clave, "antes": doc.receiver_message, "nuevo": msg, "motivo": "ya había un mensaje del cliente"},
        )
        return None, "ya había un mensaje del cliente para esa factura: se conserva el primero"
    doc.receiver_message = msg
    doc.receiver_detail = mr["detalle"]
    doc.receiver_xml = raw_text
    doc.receiver_at = datetime.now(UTC)
    if doc.receiver_message in ("2", "3"):
        _alert_client_rejection(db, tenant, doc)
    return doc, None


def _alert_client_rejection(db: Session, tenant: Tenant, doc: EInvoiceDocument) -> None:
    from html import escape

    from ..models import TenantUser
    from .email_layout import cta
    from .mail import queue_email

    inv = db.get(Invoice, doc.invoice_id)
    what = "aceptó parcialmente" if doc.receiver_message == "2" else "rechazó"
    number = inv.number if inv else doc.consecutive
    # el detalle sale del XML que llego por correo: escapado, y rotulado como no verificado
    html = (
        f"<p>Según el archivo recibido (firma no verificada), el cliente {what} la factura <b>{escape(number or '')}</b> "
        f"(clave {escape(doc.clave or '')}).</p>"
        f"<p>Detalle: {escape(doc.receiver_detail or '—')}</p><p>Revise y, si corresponde, emita la nota de crédito (Res. MH-DGT-RES-0027-2024 art. 10).</p>"
        + (cta(f"/facturas/{inv.id}", f"Abrir la factura {inv.number}") if inv else "")
    )
    to = set()
    for m in db.scalars(select(TenantUser).where(TenantUser.tenant_id == tenant.id, TenantUser.active, TenantUser.role_code == "admin")):
        if m.user.email:
            to.add(m.user.email.lower())
    if inv and inv.created_by:
        from ..models import User

        u = db.get(User, inv.created_by)
        if u and u.active and u.email:
            to.add(u.email.lower())
    for addr in sorted(to):
        queue_email(db, tenant, addr, f"El cliente {what} la factura {number}: emitir nota de crédito", html, "invoice", inv.id if inv else 0)
    audit(db, tenant.id, None, "client_receptor_message", "invoice", inv.id if inv else None, {"mensaje": doc.receiver_message, "clave": doc.clave})


def _match_pdf(name: str, lote: list[ReceivedDocument]) -> ReceivedDocument | None:
    if len(lote) == 1:
        return lote[0]
    nums = set(CLAVE_RE.findall(name)) | set(CONSEC_RE.findall(name))
    for d in lote:
        if d.clave in nums or (d.consecutive and d.consecutive in nums):
            return d
    return None


def _match_pdf_db(db: Session, tenant_id: int, name: str) -> ReceivedDocument | None:
    claves = CLAVE_RE.findall(name)
    if claves:
        d = db.scalar(select(ReceivedDocument).where(ReceivedDocument.tenant_id == tenant_id, ReceivedDocument.clave.in_(claves)))
        if d:
            return d
    cons = CONSEC_RE.findall(name)
    if cons:
        rows = db.scalars(select(ReceivedDocument).where(ReceivedDocument.tenant_id == tenant_id, ReceivedDocument.consecutive.in_(cons))).all()
        if len(rows) == 1:
            return rows[0]
    return None


def ingest_files(
    db: Session,
    tenant: Tenant,
    files: list[tuple[str, bytes]],
    *,
    source: str,
    message: ReceptionMessage | None,
    user_id: int | None,
) -> dict:
    """Registra lo que venga en un correo o lote. Devuelve el IntakeResult (y lo copia al summary del mensaje)."""
    from ..routers.media import save_media

    res = new_result()
    origin = {"imap": "correo", "manual": "manual", "historico": "historico"}.get(source, "manual")
    items: list[Item] = []
    budget = INTAKE_MAX_EXPANDED
    for name, data in files:
        if sniff(data) == "zip":
            try:
                if budget <= 0:
                    raise ZipRejected(ZIP_ERROR)
                inner = expand_zip(data, max_total=min(ZIP_MAX_TOTAL, budget))
            except ZipRejected as e:
                res["errores"].append({"name": name, "error": str(e)})
                res["summary"].append({"name": name, "kind": "zip", "result": "error", "error": str(e)})
                continue
            budget -= sum(len(b) for _, b in inner)
            items += [_classify(f"{name}/{n}", b) for n, b in inner]
        else:
            items.append(_classify(name, data))
    order = {"comprobante": 0, "mensaje_hacienda": 1, "mensaje_receptor": 2, "pdf": 3}
    items.sort(key=lambda i: order.get(i.kind, 9))
    lote: list[ReceivedDocument] = []
    for it in items:
        entry = {"name": it.name[:200], "kind": it.kind, "result": None, "clave": None, "received_document_id": None, "error": None}
        try:
            if it.error:
                entry.update(kind="xml", result="error", error=it.error)
                res["errores"].append({"name": it.name, "error": it.error})
            elif it.kind == "comprobante":
                try:
                    parsed = xml44.parse_comprobante(it.root, it.data)
                except xml44.XmlRejected as e:
                    entry.update(result="error", error=str(e))
                    res["errores"].append({"name": it.name, "error": str(e)})
                    res["summary"].append(entry)
                    continue
                raw_text = xml44.to_utf8_text(it.data, it.root)
                status, doc, reason = register_document(db, tenant, parsed, raw_text, origin=origin, message=message)
                entry["clave"] = parsed.clave
                if status == "nuevo":
                    res["nuevos"].append(doc.id)
                    lote.append(doc)
                    entry.update(result="nuevo", received_document_id=doc.id)
                    audit(db, tenant.id, user_id, "reception_intake", "received_document", doc.id, {"origen": origin, "estado": doc.status})
                elif status == "duplicado":
                    res["duplicados"].append({"clave": parsed.clave, "id": doc.id})
                    lote.append(doc)
                    entry.update(result="duplicado", received_document_id=doc.id)
                else:
                    res["ignorados"].append({"name": it.name, "reason": reason})
                    entry.update(result="ignorado", error=reason)
            elif it.kind == "mensaje_hacienda":
                mh = xml44.parse_mensaje_hacienda(it.root)
                entry["clave"] = mh["clave"]
                doc = db.scalar(select(ReceivedDocument).where(ReceivedDocument.tenant_id == tenant.id, ReceivedDocument.clave == mh["clave"])) if mh["clave"] else None
                problem = supplier_response_problem(doc, it.root) if doc is not None and not doc.xml_supplier_response else None
                if doc is None:
                    res["ignorados"].append({"name": it.name, "reason": "respuesta sin comprobante; llegará con él"})
                    entry.update(result="ignorado", error="respuesta sin comprobante; llegará con él")
                elif problem:
                    res["ignorados"].append({"name": it.name, "reason": problem})
                    entry.update(result="ignorado", received_document_id=doc.id, error=problem)
                    audit(db, tenant.id, user_id, "supplier_response_ignored", "received_document", doc.id, {"motivo": problem, "origen": origin})
                elif apply_supplier_response(db, doc, it.root, xml44.to_utf8_text(it.data, it.root)):
                    res["adjuntos"].append({"kind": "respuesta", "id": doc.id})
                    entry.update(result="respuesta", received_document_id=doc.id)
                else:
                    res["ignorados"].append({"name": it.name, "reason": "el comprobante ya tenía su respuesta de Hacienda"})
                    entry.update(result="ignorado", received_document_id=doc.id, error="ya tenía respuesta")
            elif it.kind == "mensaje_receptor":
                ed, why = apply_client_receptor_message(db, tenant, it.root, xml44.to_utf8_text(it.data, it.root))
                if ed is not None:
                    res["adjuntos"].append({"kind": "respuesta_cliente", "id": ed.id})
                    entry.update(result="respuesta_cliente", clave=ed.clave)
                else:
                    res["ignorados"].append({"name": it.name, "reason": why})
                    entry.update(result="ignorado", error=why)
            elif it.kind == "pdf":
                target = _match_pdf(it.name, lote) if lote else None
                if target is None and not lote:
                    target = _match_pdf_db(db, tenant.id, it.name)
                if target is not None and target.pdf_media_id:
                    res["ignorados"].append({"name": it.name, "reason": "el comprobante ya tenía PDF"})
                    entry.update(result="ignorado", received_document_id=target.id, error="ya tenía PDF")
                else:
                    m, _ = save_media(db, tenant.id, it.data, it.name, user_id, visibility="private", module="accounting")
                    if target is not None:
                        target.pdf_media_id = m.id
                        res["adjuntos"].append({"kind": "pdf", "id": target.id})
                        entry.update(result="pdf", received_document_id=target.id, clave=target.clave)
                    else:
                        res["sin_asignar"].append({"name": it.name, "media_id": m.id})
                        entry.update(result="sin_asignar", media_id=m.id)
            else:
                res["ignorados"].append({"name": it.name, "reason": "no es un comprobante, respuesta ni PDF"})
                entry.update(result="ignorado", error="no es un comprobante, respuesta ni PDF")
        except HTTPException as e:
            entry.update(result="error", error=str(e.detail)[:300])
            res["errores"].append({"name": it.name, "error": str(e.detail)[:300]})
        res["summary"].append(entry)
    if message is not None:
        message.summary = res["summary"]
    return res


def status_of(res: dict) -> str:
    """Estado del correo segun lo que paso con sus adjuntos."""
    ok = res["nuevos"] or res["duplicados"] or res["adjuntos"] or res["sin_asignar"]
    if res["errores"]:
        return "parcial" if ok else "error"
    if ok:
        return "procesado"
    return "sin_comprobantes"
