"""Emision fiscal (FE, TE, NC v4.4).

- Consecutivo fiscal al EMITIR (D5): de T0x en simulado/pruebas y de F0x en produccion; el numero visible no cambia.
  Reemitir tras un rechazo o error de Hacienda toma consecutivo y clave nuevos.
- Clave y FechaEmision en hora de Costa Rica (despues de las 18:00 la fecha UTC ya es la de manana).
- emit() NUNCA hace commit ni llama a la red: lo usan POST /invoices/{id}/emit y tambien la tienda dentro del
  savepoint del webhook de pago. En pruebas/produccion deja el comprobante firmado y "por_enviar"; el envio lo hace
  quien llama despues de su commit (el router) o el worker (hacienda.send_pending).
- Simulado: arma el comprobante real, lo firma con el certificado de simulacion y responde "aceptada" con la marca
  "SIMULACION · sin validez fiscal".
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..models import Customer, EInvoiceDocument, Invoice, Product, Tenant
from ..providers.einvoice import NoneProvider, build_clave, cr_now, security_code
from ..providers.hacienda import xml44
from ..providers.hacienda.provider import HaciendaDirectProvider, InvalidXml, SimulatedHacienda
from . import hacienda_config as hc
from .documents import audit
from .sequences import next_fiscal_consecutive, next_number
from .units import hacienda_unit

ROOT = {
    "FE": "FacturaElectronica",
    "TE": "TiqueteElectronico",
    "FEE": "FacturaElectronicaExportacion",
    "NC": "NotaCreditoElectronica",
    "ND": "NotaDebitoElectronica",
}
FISCAL_CODE = {"FE": "01", "ND": "02", "NC": "03", "TE": "04"}
ID_TYPE = {"fisica": "01", "juridica": "02", "dimex": "03", "nite": "04", "extranjero": "05"}
LIVE = ("por_enviar", "enviando", "enviado", "procesando", "pendiente", "aceptada")
PENDING = ("por_enviar", "enviando", "enviado", "procesando", "pendiente")
# einvoice_status de una factura cuyo comprobante se emitio FUERA de la plataforma: nunca se emite aqui
OUTSIDE = ("emitida_fuera", "importada")
SALE_CONDITIONS = {"01", "02", "03", "04", "05", "06", "07", "08", "10", "12", "13", "14", "15", "99"}
PAYMENT_METHODS = {"01", "02", "03", "04", "05", "06", "07", "99"}


def provider_for(db: Session, tenant: Tenant, purpose: str = "emision"):
    """Proveedor segun el modo vigente. purpose="recepcion": ninguno y simulado son ambos simulado.
    Devuelve None en pruebas/produccion si faltan credenciales (quien llama decide: 409 o por_enviar)."""
    mode = hc.reception_mode(db, tenant) if purpose == "recepcion" else hc.hacienda_mode(db, tenant)
    if mode == "ninguno":
        return NoneProvider()
    if mode == "simulado":
        return SimulatedHacienda()
    env = "produccion" if mode == "produccion" else "pruebas"
    creds = hc.credentials(db, tenant, env)
    if not creds:
        return None
    return HaciendaDirectProvider(tenant, env, creds, production_instance=hc.profile(db, tenant).production_instance)


# ---------------------------------------------------------------- payload
def _rate_code(db: Session, ln, tenant_id: int, lenient: bool) -> str:
    preferred = None
    if ln.product_id:
        p = db.get(Product, ln.product_id)
        if p and p.tenant_id == tenant_id:
            for pt in p.taxes or []:
                t = getattr(pt, "tax", None)
                if t is not None and Decimal(str(t.rate)).normalize() == Decimal(str(ln.tax_rate)).normalize():
                    preferred = t.rate_code
                    break
    try:
        return xml44.rate_code_for(Decimal(str(ln.tax_rate)), preferred)
    except xml44.BuildError as e:
        if lenient:
            return "10"
        raise HTTPException(422, f"Línea «{ln.name}»: {e}") from e


def _unit(ln, lenient: bool) -> str:
    u = hacienda_unit(ln.unit)
    if lenient:
        from ..providers.hacienda.xsd import schema

        try:
            allowed = set(schema("FE").types["UnidadMedidaType"].enumeration or [])
        except Exception:  # noqa: BLE001
            allowed = set()
        if allowed and u not in allowed:
            return "Unid"
    return u


def _lines(db: Session, inv: Invoice, lenient: bool) -> list[dict]:
    out = []
    for ln in inv.lines:
        if (ln.treatment or "normal") in ("aportado", "excluido"):
            continue  # aportado por el cliente y exclusiones no son parte de la venta: no viajan a Hacienda
        cabys = re.sub(r"\D", "", ln.cabys_code or "")
        if len(cabys) != 13 and lenient:
            cabys = "0" * 13  # simulado: el XML de prueba no se detiene por un CABYS faltante
        out.append(
            {
                "cabys": cabys,
                "code": ln.code,
                "quantity": str(ln.quantity),
                "unit": _unit(ln, lenient),
                "detail": ln.name,
                "unit_price": str(ln.unit_price),
                "subtotal": str(ln.subtotal),
                "tax": str(ln.tax_amount),
                "rate": str(ln.tax_rate),
                "rate_code": _rate_code(db, ln, inv.tenant_id, lenient),
            }
        )
    return out


def _activity(db: Session, tenant: Tenant, inv: Invoice, lenient: bool) -> str:
    code = (inv.activity_code or "").strip()
    if len(code) == 6:
        return code
    act = hc.default_activity(db, tenant.id)
    if act:
        return act.code
    legacy = [c for c in ((tenant.settings or {}).get("activity_codes") or []) if isinstance(c, str) and len(c) == 6]
    if legacy:
        return legacy[0]
    if lenient:
        return "000000"
    raise HTTPException(422, "Complete Ajustes → Hacienda: falta una actividad económica por defecto")


def direct_payload(
    db: Session,
    tenant: Tenant,
    inv: Invoice,
    doc_type: str,
    consecutive: str,
    clave: str,
    fecha: datetime,
    reference: dict | None = None,
    lenient: bool = False,
) -> dict:
    """Todo lo que necesita xml44.build_comprobante. lenient=True (simulado) rellena lo que falte con marcadores."""
    prof = hc.profile(db, tenant)
    c = db.get(Customer, inv.customer_id) if inv.customer_id else None
    if c is not None and c.tenant_id != inv.tenant_id:
        c = None  # un cliente de otra empresa nunca es el receptor (defensa: documents.check_customer ya lo impide)
    if doc_type in ("FE", "FEE", "NC", "ND") and not (c and c.id_number):
        raise HTTPException(422, "La factura electronica requiere receptor con identificacion (use Tiquete si es consumidor final)")
    if not lenient and inv.medical_exemption_card:
        raise HTTPException(422, "Exoneraciones: no soportado aún en emisión directa")
    receptor = None
    if c and c.id_number:
        tipo = ID_TYPE.get(c.id_type or "", "01")
        numero = c.id_number.strip() if tipo == "05" else xml44.digits(c.id_number)
        if not numero:
            raise HTTPException(422, "La identificación del cliente no es válida")
        name = (c.name or "").strip()
        receptor = {"name": name if len(name) >= 3 else (name + " ---")[:3], "id_type": tipo, "id_number": numero[:20], "email": (c.email or "").strip() or None}
    legal = (tenant.legal_name or tenant.name or "").strip()
    email = prof.email
    if lenient:
        legal = legal if len(legal) >= 5 else (legal + " SIMULADA")[:100]
        email = email or "facturacion@ejemplo.com"
    emisor = {
        "name": legal,
        "id_type": prof.id_type or "02",
        "id_number": xml44.digits(tenant.tax_id) or ("0" * 10 if lenient else ""),
        "commercial_name": prof.commercial_name,
        "province": prof.province or ("1" if lenient else None),
        "canton": prof.canton or ("01" if lenient else None),
        "district": prof.district or ("01" if lenient else None),
        "barrio": prof.barrio,
        "other_signs": prof.other_signs if prof.other_signs and len(prof.other_signs) >= 5 else ("Dirección no registrada" if lenient else None),
        "phone_country": prof.phone_country or "506",
        "phone": prof.phone,
        "email": email,
    }
    sale = inv.sale_condition or "01"
    pay = inv.payment_method or "01"
    lines = _lines(db, inv, lenient)
    return {
        "doc_type": doc_type,
        "clave": clave,
        "consecutive": consecutive,
        "fecha": fecha,
        "proveedor_sistemas": settings.hacienda_proveedor_sistemas or emisor["id_number"],
        "activity_code": _activity(db, tenant, inv, lenient),
        "emisor": emisor,
        "receptor": receptor,
        "sale_condition": sale if (sale in SALE_CONDITIONS or not lenient) else "01",
        "credit_days": inv.credit_days or 0,
        "lines": lines,
        "currency": inv.currency or "CRC",
        "fx": inv.fx_sell or 1,
        "payment_method": pay if (pay in PAYMENT_METHODS or not lenient) else "01",
        "reference": reference,
        "tax_total": inv.tax_total,
        "total": inv.total,
    }


def _check_direct_ready(db: Session, tenant: Tenant, env: str):
    falta = hc.profile_missing(db, tenant)
    if falta:
        raise HTTPException(422, "Complete Ajustes → Hacienda: falta " + ", ".join(falta))
    creds, err = hc.credentials_status(db, tenant, env)
    if not creds:
        raise HTTPException(409, err or f"Cargue las credenciales de Hacienda del ambiente {env}")
    return creds


def _sign_direct(db: Session, tenant: Tenant, env: str, creds, payload: dict) -> bytes:
    try:
        prov = HaciendaDirectProvider(tenant, env, creds, production_instance=hc.profile(db, tenant).production_instance)
        return prov.sign_document(payload)
    except xml44.BuildError as e:
        raise HTTPException(422, str(e)) from e
    except InvalidXml as e:
        raise HTTPException(422, f"El comprobante no pasa el esquema de Hacienda: {e}") from e
    except Exception as e:  # noqa: BLE001 - CertError, ProductionBlocked: legible, sin secretos
        raise HTTPException(409, f"No se pudo firmar el comprobante: {e}") from e


def _simulate(payload: dict):
    try:
        return SimulatedHacienda().emit(payload)
    except xml44.BuildError as e:
        raise HTTPException(422, str(e)) from e
    except InvalidXml as e:
        raise HTTPException(422, f"El comprobante simulado no pasa el esquema: {e}") from e


# ---------------------------------------------------------------- emitir
def outside_detail(inv: Invoice) -> str:
    if inv.einvoice_status == "importada":
        return "La factura viene del histórico importado: ya se emitió fuera de la plataforma y no se vuelve a emitir"
    numero = f", n.º {inv.external_number}" if inv.external_number else ""
    return f"La factura ya se emitió fuera de la plataforma{numero}: no se vuelve a emitir"


def emit(db: Session, tenant: Tenant, user_id: int | None, inv: Invoice) -> EInvoiceDocument:
    # la regla "ya esta pendiente o aceptada" se evalua despues del bloqueo: doble clic = un solo comprobante
    db.execute(select(Invoice.id).where(Invoice.id == inv.id).with_for_update())
    db.refresh(inv, ["einvoice_status", "status", "external_number"])
    if inv.status == "anulada":
        raise HTTPException(409, "Factura anulada")
    if inv.einvoice_status in OUTSIDE:
        # emitida en Fygaro (cola "Por emitir") o importada del historico: volver a emitirla duplicaria el comprobante
        # ante Hacienda (plan 1.7). Se revisa ANTES del modo: tampoco en produccion se reemite
        raise HTTPException(409, outside_detail(inv))
    if inv.einvoice_status in ("pendiente", "aceptada"):
        raise HTTPException(409, f"La factura ya esta {inv.einvoice_status}")
    if not inv.lines:
        raise HTTPException(422, "La factura no tiene lineas")
    mode = hc.hacienda_mode(db, tenant)
    if mode == "ninguno":
        raise HTTPException(502, "Sin proveedor fiscal configurado (Ajustes → Hacienda: elija el modo)")
    doc_type = inv.doc_type or "FE"
    if doc_type not in ("FE", "TE"):
        raise HTTPException(422, f"{doc_type}: no soportado aún en emisión directa")
    prev = db.scalars(select(EInvoiceDocument).where(EInvoiceDocument.invoice_id == inv.id, EInvoiceDocument.doc_type == doc_type)).all()
    if any(d.status in LIVE and (d.mode or "simulado") == mode for d in prev):
        raise HTTPException(409, "La factura ya tiene un comprobante vivo")
    # todo lo que sigue va en un savepoint: si algo falla despues de tomar el consecutivo (CABYS, perfil, firma) el
    # contador vuelve atras (el POS atrapa el error y confirma la venta: no puede quedar un hueco en la numeracion)
    sp = db.begin_nested()
    try:
        doc = _emit_numbered(db, tenant, user_id, inv, mode, doc_type, prev)
    except Exception:
        sp.rollback()
        raise
    sp.commit()
    return doc


def _emit_numbered(db: Session, tenant: Tenant, user_id: int | None, inv: Invoice, mode: str, doc_type: str, prev: list) -> EInvoiceDocument:
    fecha = cr_now()
    consecutive = next_fiscal_consecutive(db, tenant.id, FISCAL_CODE[doc_type], mode)
    if prev:
        audit(db, tenant.id, user_id, "einvoice_renumber", "invoice", inv.id, {"antes": inv.consecutive, "despues": consecutive})
    clave = build_clave("506", fecha, tenant.tax_id or "0", consecutive, "1", security_code())
    if mode == "simulado":
        res = _simulate(direct_payload(db, tenant, inv, doc_type, consecutive, clave, fecha, lenient=True))
        now = datetime.now(UTC)
        doc = EInvoiceDocument(
            tenant_id=tenant.id,
            invoice_id=inv.id,
            doc_type=doc_type,
            consecutive=consecutive,
            clave=clave,
            provider="sandbox",
            provider_ref=res.provider_ref,
            status="aceptada",
            hacienda_message=res.message,
            xml_document=res.xml_document,
            xml_response=res.xml_response,
            attempts=1,
            sent_at=now,
            resolved_at=now,
            mode="simulado",
            emitted_at=fecha,
        )
        db.add(doc)
        inv.consecutive, inv.clave, inv.einvoice_status = consecutive, clave, "aceptada"
        audit(db, tenant.id, user_id, "emit", "invoice", inv.id, {"status": "aceptada", "clave": clave, "provider": "sandbox", "modo": "simulado"})
        db.flush()
        return doc
    env = "produccion" if mode == "produccion" else "pruebas"
    creds = _check_direct_ready(db, tenant, env)
    # N4: en emision directa la fecha del comprobante es la de emision (hora de Costa Rica)
    if inv.issue_date != fecha.date():
        audit(db, tenant.id, user_id, "update", "invoice", inv.id, {"issue_date": [str(inv.issue_date), str(fecha.date())], "motivo": "emisión directa"})
        inv.issue_date = fecha.date()
    signed = _sign_direct(db, tenant, env, creds, direct_payload(db, tenant, inv, doc_type, consecutive, clave, fecha))
    doc = EInvoiceDocument(
        tenant_id=tenant.id,
        invoice_id=inv.id,
        doc_type=doc_type,
        consecutive=consecutive,
        clave=clave,
        provider="hacienda",
        status="por_enviar",
        xml_document=signed.decode("utf-8"),
        attempts=0,
        mode=mode,
        emitted_at=fecha,
    )
    db.add(doc)
    inv.consecutive, inv.einvoice_status = consecutive, "pendiente"
    audit(db, tenant.id, user_id, "emit", "invoice", inv.id, {"status": "por_enviar", "clave": clave, "provider": "hacienda", "modo": mode})
    db.flush()
    return doc


def credit_note(db: Session, tenant: Tenant, user_id: int, inv: Invoice, reason: str) -> EInvoiceDocument:
    """Anula una factura aceptada emitiendo una NC que referencia su clave. En simulado queda aceptada al instante; en
    directo queda por enviar y la factura se anula (y devuelve inventario) solo cuando Hacienda acepta la NC."""
    db.execute(select(Invoice.id).where(Invoice.id == inv.id).with_for_update())
    if inv.einvoice_status != "aceptada" or not inv.clave:
        raise HTTPException(409, "Solo se anula con NC una factura aceptada por Hacienda")
    open_nc = db.scalar(select(EInvoiceDocument).where(EInvoiceDocument.invoice_id == inv.id, EInvoiceDocument.doc_type == "NC", EInvoiceDocument.status.in_(LIVE)))
    if open_nc:
        raise HTTPException(409, "La factura ya tiene una nota de crédito en curso")
    mode = hc.hacienda_mode(db, tenant)
    if mode == "ninguno":
        raise HTTPException(502, "Sin proveedor fiscal configurado (Ajustes → Hacienda: elija el modo)")
    original = db.scalar(
        select(EInvoiceDocument).where(EInvoiceDocument.invoice_id == inv.id, EInvoiceDocument.clave == inv.clave).order_by(EInvoiceDocument.id.desc())
    )
    if mode in ("pruebas", "produccion"):
        mode = _nc_mode(original, mode)
    sp = db.begin_nested()
    try:
        doc = _credit_note_numbered(db, tenant, user_id, inv, reason, mode, original)
    except Exception:
        sp.rollback()
        raise
    sp.commit()
    return doc


def doc_mode(doc: EInvoiceDocument | None) -> str | None:
    """Donde se emitio un comprobante: simulado (incluye el proveedor "sandbox" de antes, sin modo), pruebas o
    produccion. None si no hay registro."""
    if doc is None:
        return None
    if doc.provider == "sandbox" or doc.mode == "simulado":
        return "simulado"
    return doc.mode


def _nc_mode(original: EInvoiceDocument | None, mode: str) -> str:
    """La NC va al mismo ambiente que su factura. Una factura que nunca llego a ESTE ambiente de Hacienda (simulada, o
    de pruebas con produccion activa) se anula aqui con una NC simulada: una NC fiscal real que referencia una clave que
    Hacienda no recibio gastaria un consecutivo y quedaria en el sistema fiscal. Una de produccion no se acredita en
    pruebas."""
    orig = doc_mode(original)
    if orig == mode:
        return mode
    if orig in ("simulado", "pruebas"):
        return "simulado"
    if orig == "produccion":
        raise HTTPException(409, "La factura se emitió en Producción: su nota de crédito se emite con Producción activa (Ajustes → Hacienda)")
    raise HTTPException(409, "No se encontró el comprobante electrónico de la factura: no se puede emitir la nota de crédito")


def _credit_note_numbered(db: Session, tenant: Tenant, user_id: int, inv: Invoice, reason: str, mode: str, original) -> EInvoiceDocument:
    number, _ = next_number(db, tenant.id, "NC")
    fecha = cr_now()
    consecutive = next_fiscal_consecutive(db, tenant.id, "03", mode)
    clave = build_clave("506", fecha, tenant.tax_id or "0", consecutive, "1", security_code())
    ref_date = (original.emitted_at if original and original.emitted_at else None) or datetime.combine(inv.issue_date, datetime.min.time())
    reference = {"doc_type": "01" if inv.doc_type == "FE" else "04", "number": inv.clave, "date": ref_date, "code": "01", "reason": reason}
    if mode == "simulado":
        res = _simulate(direct_payload(db, tenant, inv, "NC", consecutive, clave, fecha, reference=reference, lenient=True))
        now = datetime.now(UTC)
        doc = EInvoiceDocument(
            tenant_id=tenant.id,
            invoice_id=inv.id,
            doc_type="NC",
            consecutive=consecutive,
            reference_clave=inv.clave,
            clave=clave,
            provider="sandbox",
            provider_ref=res.provider_ref,
            status="aceptada",
            hacienda_message=res.message,
            xml_document=res.xml_document,
            xml_response=res.xml_response,
            attempts=1,
            sent_at=now,
            resolved_at=now,
            mode="simulado",
            emitted_at=fecha,
        )
        db.add(doc)
        inv.status = "anulada"
        audit(db, tenant.id, user_id, "void_nc", "invoice", inv.id, {"nc": number, "clave": clave, "reason": reason, "modo": "simulado"})
        db.flush()
        return doc
    env = "produccion" if mode == "produccion" else "pruebas"
    creds = _check_direct_ready(db, tenant, env)
    signed = _sign_direct(db, tenant, env, creds, direct_payload(db, tenant, inv, "NC", consecutive, clave, fecha, reference=reference))
    doc = EInvoiceDocument(
        tenant_id=tenant.id,
        invoice_id=inv.id,
        doc_type="NC",
        consecutive=consecutive,
        reference_clave=inv.clave,
        clave=clave,
        provider="hacienda",
        status="por_enviar",
        xml_document=signed.decode("utf-8"),
        attempts=0,
        mode=mode,
        emitted_at=fecha,
        hacienda_message=f"NC {number}: {reason}"[:300],
    )
    db.add(doc)
    audit(db, tenant.id, user_id, "void_nc_request", "invoice", inv.id, {"nc": number, "clave": clave, "reason": reason, "modo": mode})
    db.flush()
    return doc


def documents_for(db: Session, inv: Invoice) -> list[EInvoiceDocument]:
    return db.scalars(select(EInvoiceDocument).where(EInvoiceDocument.invoice_id == inv.id).order_by(EInvoiceDocument.id)).all()


def last_document(db: Session, inv: Invoice) -> EInvoiceDocument | None:
    return db.scalar(select(EInvoiceDocument).where(EInvoiceDocument.invoice_id == inv.id).order_by(EInvoiceDocument.id.desc()))


def is_direct(doc: EInvoiceDocument | None) -> bool:
    return bool(doc and doc.mode in ("pruebas", "produccion"))
