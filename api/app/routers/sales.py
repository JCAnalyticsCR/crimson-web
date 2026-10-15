"""Cotizaciones, facturas, pagos manuales, enlace de pago y dashboard."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, or_, select, true
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import BillingGroup, Customer, Invoice, Payment, PaymentLink, Quote
from ..schemas.sales import (
    DocListItem,
    DocumentIn,
    InvoiceOut,
    PaymentIn,
    PaymentLinkOut,
    PaymentOut,
    QuoteOut,
)
from ..services import discounts, units
from ..services import documents as svc
from ..services import inventory as invsvc
from ..services import quote_versions as qv
from ..services.documents import audit
from ..services.ownership import doc_mine, doc_scope, eligible_seller, owns_doc, payment_scope
from ..services.totals import compute_document, d

router = APIRouter(tags=["ventas"])


def _cust_name(db: Session, cid: int | None) -> str | None:
    if not cid:
        return None
    c = db.get(Customer, cid)
    return c.name if c else None


def _user_name(db: Session, uid: int | None) -> str | None:
    from ..models import User

    u = db.get(User, uid) if uid else None
    return u.full_name if u else None


_SIN_DOC = object()  # _seller_from_body al crear: todavia no hay vendedor con que comparar


def _check_seller(db: Session, p: Principal, uid: int) -> int:
    """Vendedor elegible: usuario ACTIVO del equipo de esta empresa (ni de un cliente ni de un aliado)."""
    if not eligible_seller(db, p.tenant.id, uid):
        raise HTTPException(422, "Ese vendedor no es un usuario activo de la empresa")
    return uid


def _seller_from_body(db: Session, p: Principal, data: DocumentIn, current=_SIN_DOC):
    """(cambia, vendedor). Elegir el vendedor es de quien tiene sales.asignar (admin); al resto se le ignora en
    silencio (el portal viejo manda el campo vacio). None = venta sin vendedor.
    Con `current` (el vendedor que ya tiene el documento) solo se valida si cambia: el editor manda el vendedor en
    cada guardado y uno que ya no esta activo no impide corregir una linea (se reasigna aparte, caso borde 7.4)."""
    if "seller_id" not in data.model_fields_set or not p.can("sales", "asignar"):
        return False, None
    if current is not _SIN_DOC and data.seller_id == current:
        return False, None
    return True, (_check_seller(db, p, data.seller_id) if data.seller_id is not None else None)


def _set_seller(db: Session, p: Principal, doc, data: DocumentIn, entity: str) -> None:
    """Cambiar el vendedor de un documento ya creado (solo admin); queda en la auditoria."""
    cambia, nuevo = _seller_from_body(db, p, data, current=doc.seller_id)
    if cambia:
        antes, doc.seller_id = doc.seller_id, nuevo
        audit(db, p.tenant.id, p.user.id, "assign_seller", entity, doc.id, {"de": antes, "a": nuevo}, ip=p.ip)


# Las cotizaciones hechas desde un levantamiento antes de la matriz de roles (13/10) guardaban en las notas internas
# "Costo estimado ₡X · margen Y %". Las notas las lee todo el que ve el documento (contador, caja, solo lectura) y
# pasan a la factura al convertir: a quien no ve costos se le quitan esas cifras al salir (ver tambien fieldwork).
_COSTO_EN_NOTAS = re.compile(r"\s*Costo estimado\s*₡[^·\n]*·\s*margen[^%\n]*%\.?", re.IGNORECASE)


def _notes_for(p: Principal, notes: str | None) -> str | None:
    if not notes or p.sees_costs:
        return notes
    return _COSTO_EN_NOTAS.sub("", notes).strip() or None


def _quote_out(db: Session, q: Quote, p: Principal) -> QuoteOut:
    from ..models import Opportunity

    o = QuoteOut.model_validate(q)
    o.customer_name = _cust_name(db, q.customer_id)
    o.seller_name = _user_name(db, q.seller_id)
    o.internal_notes = _notes_for(p, q.internal_notes)
    # el margen deja deducir el costo: quien no ve costos lee el motivo sin cifras (el texto guardado no cambia)
    o.approval_reason = discounts.public_reason(q.approval_reason, p.sees_costs)
    # el editor necesita saber si la cotizacion es de una oportunidad de venta (no ofrece "convertir a proyecto")
    opp = db.scalar(select(Opportunity).where(Opportunity.tenant_id == q.tenant_id, Opportunity.quote_id == q.id))
    o.opportunity = {"id": opp.id, "number": opp.number, "kind": opp.kind or "proyecto", "status": opp.status} if opp else None
    if opp:
        from ..services.allies import party_names

        # empresas que pueden aportar equipo (selector "Aportado por" del editor); nunca sale al portal del cliente
        o.opportunity["parties"] = party_names(db, opp)
    st = qv.state(db, q)
    o.current_version, o.has_unsent_changes = st["current_version"], st["has_unsent_changes"]
    return o


def _inv_out(db: Session, i: Invoice, p: Principal) -> InvoiceOut:
    o = InvoiceOut.model_validate(i)
    o.customer_name = _cust_name(db, i.customer_id)
    o.seller_name = _user_name(db, i.seller_id)
    o.internal_notes = _notes_for(p, i.internal_notes)
    return o


# ---------- Acciones pendientes del inicio ----------
# Cada tarjeta de "Acciones pendientes" lleva a la lista filtrada con ?pendiente=<clave>. El numero de la tarjeta y
# las filas de la lista salen de ESTA misma definicion, asi no pueden decir cosas distintas.
PENDIENTES = {
    "facturas_vencidas": "invoices",
    "facturas_por_cobrar": "invoices",
    "documentos_rechazados": "invoices",
    "enlaces_abiertos": "invoices",
    "cotizaciones_sin_respuesta": "quotes",
    "cotizaciones_por_aprobar": "quotes",
}


def _pend_mine(p: Principal) -> bool:
    """Solo lo propio si el inicio es personal (sin dashboard.empresa) o si la lista lo es (sin sales.ver_todo):
    la tarjeta nunca cuenta filas que la lista no puede mostrar."""
    return not p.can("dashboard", "empresa") or not p.sees_all_sales


def pending_where(key: str, p: Principal) -> list:
    tid = p.tenant.id
    model = Invoice if PENDIENTES[key] == "invoices" else Quote
    out = [model.tenant_id == tid]
    if _pend_mine(p):
        out.append(doc_mine(model, p.user.id))
    today = date.today()
    if key == "facturas_vencidas":
        out += [Invoice.status.in_(("creado", "parcial", "vencida")), Invoice.due_date < today]
    elif key == "facturas_por_cobrar":
        out.append(Invoice.status.in_(("creado", "parcial")))
    elif key == "documentos_rechazados":
        out.append(Invoice.einvoice_status.in_(("rechazada", "error")))  # error: Hacienda no respondio o no lo encontro
    elif key == "enlaces_abiertos":
        # facturas con un enlace de pago vigente sin pagar (una factura con dos enlaces cuenta una vez)
        abiertos = select(PaymentLink.invoice_id).where(PaymentLink.tenant_id == tid, PaymentLink.paid_at.is_(None), PaymentLink.expires_at > datetime.now(UTC))
        out.append(Invoice.id.in_(abiertos))
    elif key == "cotizaciones_sin_respuesta":
        out.append(Quote.status.in_(("creado", "enviada")))
    elif key == "cotizaciones_por_aprobar":
        out.append(Quote.status == "por_aprobar")
    return out


def pending_count(db: Session, key: str, p: Principal) -> int:
    model = Invoice if PENDIENTES[key] == "invoices" else Quote
    return db.scalar(select(func.count()).select_from(model).where(*pending_where(key, p))) or 0


def _list(
    db: Session,
    model,
    tenant_id: int,
    status: str | None,
    cursor: int | None,
    limit: int,
    q: str | None,
    p: Principal | None = None,
    pendiente: str | None = None,
):
    stmt = select(model).where(model.tenant_id == tenant_id)
    if p is not None:
        stmt = stmt.where(doc_scope(model, p))  # primero lo mio: la busqueda nunca sale de ahi
    if pendiente:
        kind = "invoices" if model is Invoice else "quotes"
        if PENDIENTES.get(pendiente) != kind or p is None:
            raise HTTPException(422, "Filtro de pendientes desconocido")
        stmt = stmt.where(*pending_where(pendiente, p))
    if status:
        stmt = stmt.where(model.status == status)
    txt = (q or "").strip()[:60]
    if txt:
        # numero, consecutivo o clave de Hacienda (facturas) y nombre del cliente. % y _ se buscan literales.
        like = f"%{_esc_like(txt)}%"
        conds = [model.number.ilike(like, escape="\\")]
        if model is Invoice:
            conds += [Invoice.consecutive.ilike(like, escape="\\"), Invoice.clave.ilike(like, escape="\\")]
        clientes = select(Customer.id).where(Customer.tenant_id == tenant_id, Customer.name.ilike(like, escape="\\"))
        conds.append(model.customer_id.in_(clientes))
        stmt = stmt.where(or_(*conds))
    if cursor:
        stmt = stmt.where(model.id < cursor)
    rows = db.scalars(stmt.order_by(model.updated_at.desc(), model.id.desc()).limit(limit + 1)).all()
    page = list(rows[:limit])
    if txt.isdigit():
        # "4" o "004": primero el documento cuyo numero termina exactamente en ese consecutivo (...-000004); el resto
        # (...-000040, ...-000400) sigue en su orden. Solo reordena la pagina: el cursor no cambia.
        n = int(txt)
        page.sort(key=lambda r: 0 if _seq_of(r.number) == n else 1)
    items = []
    for r in page:
        it = DocListItem.model_validate(r)
        it.customer_name = _cust_name(db, r.customer_id)
        it.seller_name = _user_name(db, r.seller_id)
        items.append(it)
    return {"items": items, "next_cursor": rows[limit].id if len(rows) > limit else None}


def _esc_like(txt: str) -> str:
    """Comodines de LIKE como texto: buscar "%" no devuelve todo."""
    return txt.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _seq_of(number: str | None) -> int | None:
    """COT-000008 -> 8. Sin sufijo numerico, None."""
    m = re.search(r"(\d+)$", number or "")
    return int(m.group(1)) if m else None


def _own(db: Session, model, id_: int, tenant_id: int, p: Principal | None = None):
    obj = db.get(model, id_)
    # lo ajeno no existe (404, nunca 403): ni siquiera se confirma que el numero exista
    if not obj or obj.tenant_id != tenant_id or (p is not None and not owns_doc(obj, p)):
        raise HTTPException(404, "Documento no encontrado")
    return obj


# ---------- Preview de totales (editor en vivo, sin guardar) ----------
@router.post("/documents/preview")
def preview(data: DocumentIn, p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    """Totales en vivo para el editor (sin guardar). Completa precio/impuesto desde el producto si falta."""
    lines = [svc._fill_line_from_product(db, p.tenant.id, ln) for ln in data.lines]
    calc = compute_document(
        [svc.line_in(ln) for ln in lines],
        data.discount_type,
        data.discount_value,
    )
    return {
        "subtotal": calc.subtotal,
        "discount_total": calc.discount_total,
        "tax_total": calc.tax_total,
        "total": calc.total,
        "taxable_by_rate": calc.taxable_by_rate,
        "lines": [
            {
                "name": ln.name,
                "code": ln.code,
                "unit_price": ln.unit_price,
                "tax_rate": ln.tax_rate,
                "subtotal": lo.subtotal,
                "tax_amount": lo.tax_amount,
                "total": lo.total,
                "treatment": ln.treatment,
            }
            for ln, lo in zip(lines, calc.lines, strict=True)
        ],
    }


# ---------- Cotizaciones ----------
@router.get("/quotes")
def quotes(
    status: str | None = None,
    q: str | None = None,
    cursor: int | None = None,
    limit: int = Query(20, le=100),
    pendiente: str | None = None,
    p: Principal = Depends(require("sales", "ver")),
    db: Session = Depends(get_db),
):
    return _list(db, Quote, p.tenant.id, status, cursor, limit, q, p, pendiente)


@router.post("/quotes", response_model=QuoteOut, status_code=201)
def create_quote(data: DocumentIn, p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    chk = discounts.check(db, p, data)
    cambia, vendedor = _seller_from_body(db, p, data)
    q = svc.create_quote(db, p.tenant.id, p.user.id, data, **({"seller_id": vendedor} if cambia else {}))
    _hold(db, p, q, chk)
    db.commit()
    return _quote_out(db, q, p)


def _hold(db: Session, p: Principal, q, chk) -> list[str]:
    """Deja la cotizacion en "por_aprobar" si excede el descuento, el monto o el margen minimo."""
    razones = ([chk.message] if chk.exceeds else []) + discounts.approval_reasons(db, p, q)
    if p.can("sales", "aprobar"):
        # quien aprueba no se pide permiso: lo que guarda con el margen bajo el minimo queda aprobado a esos precios (la
        # vendedora la abre y la envia sin que vuelva a quedar retenida; discounts.margin_covered)
        ok = discounts.margin_approval(db, p.tenant, q)
        if ok and not discounts.margin_covered(db, p.tenant.id, q):
            audit(db, p.tenant.id, p.user.id, "approve_margin", "quote", q.id, {"margen_aprobado": ok}, ip=p.ip)
    else:
        m = discounts.quote_margin(db, p.tenant.id, q)
        cerca = not p.sees_costs and discounts.near_min(p.tenant, m)
        por_margen = any(discounts.margin_sentence(r) for r in razones)
        # aprobada con margen bajo y ningun precio por debajo de lo aprobado: no se vuelve a retener por el margen ni
        # cuenta como sondeo (antes «Guardar & enviar» la volvia a retener y alimentaba el freno de abajo)
        cubierta = (por_margen or cerca) and discounts.margin_covered(db, p.tenant.id, q)
        if cubierta:
            razones = [r for r in razones if not discounts.margin_sentence(r)]
            por_margen = False
        if not p.sees_costs:
            # Retenida por margen, quien no ve costos no la suelta subiendo el precio: si se soltara, unos cuantos
            # guardados (subir, bajar, ver si queda retenida) darian el precio exacto donde el margen llega al minimo, y
            # de ahi el costo. La retencion por margen la suelta quien aprueba (o volver a los precios que aprobo); la de
            # descuento sigue soltandose sola.
            previa = discounts.margin_sentence(q.approval_reason) if q.status == "por_aprobar" and not cubierta else None
            if previa and not por_margen:
                razones.append(previa)
            # Y no se sondea guardando precios distintos: cada margen distinto cerca del minimo (retenga o no) cuenta, y
            # pasado el tope todo lo suyo queda retenido sin decir si fue el margen (discounts.margin_probe). Va primero:
            # el recorte a 400 caracteres no lo pierde.
            informa = cerca and not cubierta and not previa
            if discounts.margin_probe(db, p, q, m if informa else None, held=por_margen):
                razones.insert(0, discounts.PROBE_REASON)
    if razones:
        q.status = "por_aprobar"
        q.approval_reason = " ".join(razones)[:400]
        audit(db, p.tenant.id, p.user.id, "needs_approval", "quote", q.id, {"razones": razones}, ip=p.ip)
    elif q.status == "por_aprobar":
        q.status = "creado"  # quedo dentro de los limites (o lo edito quien aprueba)
        q.approval_reason = None
    return razones


@router.get("/quotes/{qid}", response_model=QuoteOut)
def get_quote(qid: int, p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    return _quote_out(db, _own(db, Quote, qid, p.tenant.id, p), p)


@router.put("/quotes/{qid}", response_model=QuoteOut)
def update_quote(qid: int, data: DocumentIn, p: Principal = Depends(require("sales", "editar")), db: Session = Depends(get_db)):
    q = _own(db, Quote, qid, p.tenant.id, p)
    if q.status in ("convertida", "anulada"):
        raise HTTPException(409, f"La cotizacion esta {q.status} y no se puede editar")
    if q.acceptance_status == "aceptada":
        raise HTTPException(
            409,
            f"El cliente ya aceptó la versión {q.accepted_version} de esta cotización. Para cambiarla, deshacé la aceptación en Versiones o duplicala.",
        )
    from ..models import QuoteLine

    chk = discounts.check(db, p, data)
    svc.apply_document(db, q, data, QuoteLine, p.tenant.id)
    _set_seller(db, p, q, data, "quote")
    db.flush()  # los totales nuevos son los que miran las reglas de aprobacion
    _hold(db, p, q, chk)
    from .pipeline import sync_amount_from_quote

    sync_amount_from_quote(db, q)  # la oportunidad ligada toma el total nuevo
    audit(db, p.tenant.id, p.user.id, "update", "quote", q.id, {"discount_pct": str(chk.worst)} if chk.worst else None, ip=p.ip)
    db.commit()
    return _quote_out(db, q, p)


@router.post("/quotes/{qid}/convert", response_model=InvoiceOut, status_code=201)
def convert_quote(qid: int, p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    q = _own(db, Quote, qid, p.tenant.id, p)
    if q.status == "por_aprobar":
        raise HTTPException(409, "La cotización tiene un descuento pendiente de aprobación")
    _use_accepted(db, p, q)
    inv = svc.convert_quote(db, p.tenant.id, p.user.id, q)
    invsvc.deduct_for_invoice(db, inv, p.user.id)
    from ..models import Opportunity

    # venta de equipo: la factura es el cierre, la oportunidad queda ganada. En "proyecto" la gana el proyecto.
    for o in db.scalars(select(Opportunity).where(Opportunity.tenant_id == p.tenant.id, Opportunity.quote_id == q.id, Opportunity.kind == "venta")):
        if o.status not in ("ganada", "perdida"):
            audit(db, p.tenant.id, p.user.id, "status", "opportunity", o.id, {"de": o.status, "a": "ganada"}, ip=p.ip)
            o.status = "ganada"
    db.commit()
    return _inv_out(db, inv, p)


def _use_accepted(db: Session, p: Principal, q: Quote) -> None:
    """Convertir usa lo que el cliente acepto. Rechazada no se convierte; aceptada con diferencias se restaura."""
    if q.acceptance_status == "rechazada":
        raise HTTPException(409, "El cliente rechazó esta cotización. Registrá la aceptación (o enviá una versión nueva) antes de convertirla.")
    if q.acceptance_status == "aceptada" and q.accepted_version:
        v = qv.get_version(db, q, q.accepted_version)
        if v and v.content_hash != qv.live_hash(db, q):
            qv.restore(db, q, v)
            audit(db, p.tenant.id, p.user.id, "restore_version", "quote", q.id, {"version": v.version}, ip=p.ip)


@router.post("/quotes/{qid}/duplicate", response_model=QuoteOut, status_code=201)
def duplicate_quote(qid: int, p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    q = _own(db, Quote, qid, p.tenant.id, p)
    payload = svc.quote_to_payload(q)
    # la copia es del mismo vendedor (la duplica la oficina para la vendedora y le sigue apareciendo a ella)
    nq = svc.create_quote(db, p.tenant.id, p.user.id, payload, seller_id=q.seller_id if q.seller_id is not None else p.user.id)
    # la copia pasa por las mismas reglas que una nueva (descuento, monto y margen): antes solo miraba el descuento y
    # duplicar una retenida por margen o por monto daba una copia lista para enviar sin aprobacion
    _hold(db, p, nq, discounts.check(db, p, payload))
    db.commit()
    return _quote_out(db, nq, p)


@router.post("/quotes/{qid}/void", response_model=QuoteOut)
def void_quote(qid: int, p: Principal = Depends(require("sales", "anular")), db: Session = Depends(get_db)):
    q = _own(db, Quote, qid, p.tenant.id, p)
    if q.status == "convertida":
        raise HTTPException(409, "Una cotizacion convertida no se anula; anule la factura")
    q.status = "anulada"
    from ..services import commissions as comsvc

    comsvc.on_quote_void(db, q, p.user.id)
    audit(db, p.tenant.id, p.user.id, "void", "quote", q.id, ip=p.ip)
    db.commit()
    return _quote_out(db, q, p)


class MarkSentIn(BaseModel):
    channel: str = Field("manual", pattern="^(manual|whatsapp|correo|presencial)$")
    to: str | None = Field(None, max_length=200)  # a quien se le entrego (telefono, nombre, correo)


@router.post("/quotes/{qid}/send", response_model=QuoteOut)
def send_quote(qid: int, data: MarkSentIn | None = None, p: Principal = Depends(require("sales", "enviar")), db: Session = Depends(get_db)):
    """Marca como enviada por fuera del correo (WhatsApp, en mano). Igual que el correo, guarda la version enviada."""
    data = data or MarkSentIn()
    q = _own(db, Quote, qid, p.tenant.id, p)
    if q.status == "por_aprobar":
        raise HTTPException(409, "La cotización tiene un descuento pendiente de aprobación")
    if q.status in ("convertida", "anulada"):
        raise HTTPException(409, f"La cotización está {q.status}")
    units.check_ready(q, "enviar la cotización")
    if q.status == "creado":
        q.status = "enviada"
    v, _ = qv.record_send(db, p.tenant, p.user.id, q, data.channel, data.to or _cust_name(db, q.customer_id), ip=p.ip)
    audit(db, p.tenant.id, p.user.id, "send", "quote", q.id, {"version": v.version, "canal": data.channel}, ip=p.ip)
    db.commit()
    return _quote_out(db, q, p)


# ---------- Versiones y aceptacion ----------
@router.get("/quotes/{qid}/versions")
def quote_versions(qid: int, p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    from ..models import QuoteAcceptance, User

    q = _own(db, Quote, qid, p.tenant.id, p)
    hist = db.scalars(select(QuoteAcceptance).where(QuoteAcceptance.quote_id == q.id).order_by(QuoteAcceptance.id.desc())).all()

    def _who(uid):
        u = db.get(User, uid) if uid else None
        return u.full_name if u else None

    return {
        "acceptance_status": q.acceptance_status,
        "accepted_version": q.accepted_version,
        "status": q.status,
        **qv.state(db, q),
        "versions": [qv.version_out(db, v, q) for v in reversed(qv.versions(db, q))],
        "acceptances": [
            {
                "id": a.id,
                "status": a.status,
                "version": a.version,
                "contact_name": a.contact_name,
                "channel": a.channel,
                "decided_on": a.decided_on,
                "notes": a.notes,
                "recorded_by": _who(a.recorded_by),
                "recorded_at": a.recorded_at,
            }
            for a in hist
        ],
    }


def _version_or_404(db: Session, q: Quote, n: int):
    v = qv.get_version(db, q, n)
    if not v:
        raise HTTPException(404, "Versión no encontrada")
    return v


@router.get("/quotes/{qid}/versions/{n}/html", response_class=HTMLResponse)
def quote_version_html(qid: int, n: int, p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    from ..services.render import render_snapshot_html

    q = _own(db, Quote, qid, p.tenant.id, p)
    return render_snapshot_html(_version_or_404(db, q, n).snapshot, p.tenant, n)


@router.get("/quotes/{qid}/versions/{n}/pdf")
def quote_version_pdf(qid: int, n: int, p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    from ..services.render import render_pdf, render_snapshot_html

    q = _own(db, Quote, qid, p.tenant.id, p)
    html = render_snapshot_html(_version_or_404(db, q, n).snapshot, p.tenant, n)
    pdf = render_pdf(html)
    if pdf is None:  # sin WeasyPrint (Windows local): el navegador imprime el HTML
        return HTMLResponse(html, headers={"X-PDF-Fallback": "html"})
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{q.number}-v{n}.pdf"'})


class AcceptanceIn(BaseModel):
    status: str = Field(pattern="^(aceptada|rechazada|pendiente)$")  # pendiente = deshacer la ultima respuesta
    version: int | None = None  # sin dato: la ultima enviada
    contact_name: str | None = Field(None, max_length=160)
    channel: str | None = Field(None, pattern="^(correo|whatsapp|firma|portal|telefono|presencial)$")
    decided_on: date | None = None
    notes: str | None = Field(None, max_length=2000)


@router.post("/quotes/{qid}/acceptance", response_model=QuoteOut)
def quote_acceptance(qid: int, data: AcceptanceIn, p: Principal = Depends(require("sales", "editar")), db: Session = Depends(get_db)):
    """Registra la respuesta del cliente: quien, por que medio, cuando y sobre que version. Queda en bitacora."""
    from ..models import QuoteAcceptance

    q = _own(db, Quote, qid, p.tenant.id, p)
    if q.status in ("convertida", "anulada"):
        raise HTTPException(409, f"La cotización está {q.status}; su aceptación ya no se cambia")
    v = None
    if data.status != "pendiente":
        if not (data.contact_name or "").strip():
            raise HTTPException(422, "Indicá quién respondió del lado del cliente")
        if not data.channel:
            raise HTTPException(422, "Indicá el medio: correo, WhatsApp, firma, portal, teléfono o en persona")
        if data.decided_on and data.decided_on > date.today():
            raise HTTPException(422, "La fecha de respuesta no puede ser futura")
        v = qv.get_version(db, q, data.version) if data.version else qv.latest(db, q)
        if not v:
            raise HTTPException(409, "La cotización no tiene versiones enviadas: enviala al cliente antes de registrar su respuesta")
    elif q.acceptance_status == "pendiente":
        raise HTTPException(409, "No hay una respuesta del cliente que deshacer")
    restored = False
    if data.status == "aceptada" and v.content_hash != qv.live_hash(db, q):
        # lo que se acepto manda: la cotizacion viva vuelve a esa version (los cambios sin enviar se descartan)
        qv.restore(db, q, v)
        restored = True
    db.add(
        QuoteAcceptance(
            tenant_id=p.tenant.id,
            quote_id=q.id,
            version_id=v.id if v else None,
            version=v.version if v else None,
            status=data.status,
            contact_name=(data.contact_name or "").strip() or None,
            channel=data.channel,
            decided_on=data.decided_on or (date.today() if v else None),
            notes=data.notes,
            recorded_by=p.user.id,
        )
    )
    antes = q.acceptance_status
    q.acceptance_status = data.status
    q.accepted_version = v.version if data.status == "aceptada" else None
    from ..services import commissions as comsvc

    comsvc.on_quote_acceptance(db, q, antes, data.status, data.decided_on)
    audit(
        db,
        p.tenant.id,
        p.user.id,
        "acceptance",
        "quote",
        q.id,
        {"de": antes, "a": data.status, "version": v.version if v else None, "quien": data.contact_name, "medio": data.channel, "restaurada": restored},
        ip=p.ip,
    )
    db.commit()
    db.refresh(q)
    return _quote_out(db, q, p)


@router.post("/quotes/{qid}/approve", response_model=QuoteOut)
def approve_quote(qid: int, p: Principal = Depends(require("sales", "aprobar")), db: Session = Depends(get_db)):
    """Un administrador aprueba el descuento que excede el limite del vendedor."""
    q = _own(db, Quote, qid, p.tenant.id, p)
    if q.status != "por_aprobar":
        raise HTTPException(409, "La cotización no está pendiente de aprobación")
    q.status = "creado"
    motivo, q.approval_reason = q.approval_reason, None
    diff = {"motivo": motivo}
    # con el margen bajo el minimo: queda aprobado a estos precios (volver a guardarla sin bajarlos no la retiene otra vez)
    ok = discounts.margin_approval(db, p.tenant, q)
    if ok:
        diff["margen_aprobado"] = ok
    audit(db, p.tenant.id, p.user.id, "approve_quote", "quote", q.id, diff, ip=p.ip)
    db.commit()
    return _quote_out(db, q, p)


@router.get("/sales/discount-limit")
def discount_limit(p: Principal = Depends(require("sales", "ver"))):
    return {
        "limit": discounts.max_discount(p.tenant),
        "free": p.can("sales", "descuento_libre"),
        "max_amount": discounts.approval_amount(p.tenant),
        # el margen minimo solo a quien ve costos: con el y el precio donde una cotizacion empieza a quedar retenida
        # por margen se saca el costo (precio x (1 - minimo))
        "min_margin": discounts.min_margin(p.tenant) if p.sees_costs else None,
        "approves": p.can("sales", "aprobar"),
    }


# ---------- Facturas ----------
@router.get("/invoices")
def invoices(
    status: str | None = None,
    q: str | None = None,
    cursor: int | None = None,
    limit: int = Query(20, le=100),
    pendiente: str | None = None,
    p: Principal = Depends(require("sales", "ver")),
    db: Session = Depends(get_db),
):
    return _list(db, Invoice, p.tenant.id, status, cursor, limit, q, p, pendiente)


def _no_pending(data: DocumentIn) -> None:
    """Una factura no lleva lineas con precio pendiente: eso se resuelve en la cotizacion."""
    pend = [ln.name for ln in data.lines if ln.treatment == "pendiente"]
    if pend:
        raise HTTPException(422, f"La factura tiene líneas con precio pendiente ({', '.join(pend[:6])}). Completá el precio antes de facturar.")


@router.post("/invoices", response_model=InvoiceOut, status_code=201)
def create_invoice(data: DocumentIn, doc_type: str = "FE", p: Principal = Depends(require("sales", "crear")), db: Session = Depends(get_db)):
    if doc_type not in ("FE", "TE", "FEE"):
        raise HTTPException(422, "doc_type debe ser FE, TE o FEE")
    _no_pending(data)
    chk = discounts.check(db, p, data)
    if chk.exceeds:
        raise HTTPException(422, f"{chk.message} Hacé una cotización para que un administrador apruebe el descuento.")
    cambia, vendedor = _seller_from_body(db, p, data)
    inv = svc.create_invoice(db, p.tenant.id, p.user.id, data, doc_type, **({"seller_id": vendedor} if cambia else {}))
    invsvc.deduct_for_invoice(db, inv, p.user.id)
    db.commit()
    return _inv_out(db, inv, p)


@router.get("/invoices/{iid}", response_model=InvoiceOut)
def get_invoice(iid: int, p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    return _inv_out(db, _own(db, Invoice, iid, p.tenant.id, p), p)


@router.put("/invoices/{iid}", response_model=InvoiceOut)
def update_invoice(iid: int, data: DocumentIn, p: Principal = Depends(require("sales", "editar")), db: Session = Depends(get_db)):
    inv = _own(db, Invoice, iid, p.tenant.id, p)
    if inv.status == "anulada" or inv.einvoice_status in ("pendiente", "aceptada"):
        raise HTTPException(409, "La factura ya fue emitida o anulada; use nota de credito")
    if inv.payments:
        raise HTTPException(409, "La factura tiene pagos registrados")
    _no_pending(data)
    from ..models import InvoiceLine

    chk = discounts.check(db, p, data)
    if chk.exceeds:
        raise HTTPException(422, f"{chk.message} Hacé una cotización para que un administrador apruebe el descuento.")
    svc.apply_document(db, inv, data, InvoiceLine, p.tenant.id)
    # con pagos o emitida ya respondio 409 arriba: el vendedor (y su comision) no se mueve en una factura cobrada
    _set_seller(db, p, inv, data, "invoice")
    audit(db, p.tenant.id, p.user.id, "update", "invoice", inv.id, ip=p.ip)
    db.commit()
    return _inv_out(db, inv, p)


@router.post("/invoices/{iid}/void", response_model=InvoiceOut)
def void_invoice(iid: int, p: Principal = Depends(require("sales", "anular")), db: Session = Depends(get_db)):
    inv = _own(db, Invoice, iid, p.tenant.id, p)
    if inv.status == "anulada":  # anular dos veces devolvia el inventario dos veces
        raise HTTPException(409, "La factura ya está anulada")
    if inv.einvoice_status == "aceptada":
        raise HTTPException(409, "Factura aceptada por Hacienda: se anula emitiendo Nota de Credito (Fase 2)")
    from ..models import EInvoiceDocument
    from ..services.einvoice import LIVE

    enviada = db.scalar(
        select(EInvoiceDocument.id).where(
            EInvoiceDocument.tenant_id == p.tenant.id,
            EInvoiceDocument.invoice_id == inv.id,
            EInvoiceDocument.doc_type != "NC",
            EInvoiceDocument.provider == "hacienda",
            or_(EInvoiceDocument.status.in_(LIVE), and_(EInvoiceDocument.status == "error", EInvoiceDocument.attempts > 0)),
        )
    )
    if enviada or inv.einvoice_status == "pendiente":
        # el worker la sigue mandando y consultando, y si Hacienda ya la acepto el IVA debito existe: anularla aqui la
        # sacaria del D-104 y la NC ya no se ofreceria. Se espera la respuesta y, si la acepto, se anula con NC
        raise HTTPException(
            409,
            "El comprobante electrónico ya se envió a Hacienda y su estado no está confirmado: consultá el estado; si Hacienda lo aceptó, se anula con nota de crédito",
        )
    inv.status = "anulada"
    invsvc.restock_for_void(db, inv, p.user.id)
    from ..services import commissions as comsvc

    comsvc.on_invoice_void(db, inv, p.user.id)  # lo devengado de una factura anulada se anula o se revierte (falla E)
    audit(db, p.tenant.id, p.user.id, "void", "invoice", inv.id, ip=p.ip)
    db.commit()
    return _inv_out(db, inv, p)


@router.post("/invoices/{iid}/payments", response_model=InvoiceOut, status_code=201)
def pay_invoice(iid: int, data: PaymentIn, p: Principal = Depends(require("payments", "crear")), db: Session = Depends(get_db)):
    inv = _own(db, Invoice, iid, p.tenant.id, p)
    svc.add_payment(db, p.tenant.id, p.user.id, inv, data)
    db.commit()
    db.refresh(inv)
    return _inv_out(db, inv, p)


@router.post("/invoices/{iid}/payment-link", response_model=PaymentLinkOut)
def payment_link(iid: int, p: Principal = Depends(require("sales", "enviar")), db: Session = Depends(get_db)):
    inv = _own(db, Invoice, iid, p.tenant.id, p)
    link = svc.get_or_create_payment_link(db, p.tenant.id, inv)
    db.commit()
    return PaymentLinkOut(
        url=link.url,
        whatsapp_url=svc.whatsapp_share_url(inv, link.url, _cust_name(db, inv.customer_id), p.tenant.name),
        expires_at=link.expires_at.isoformat(),
        opened_count=link.opened_count,
    )


# ---------- Pagos (listado) ----------
@router.get("/payments", response_model=list[PaymentOut])
def payments(limit: int = Query(20, le=100), p: Principal = Depends(require("payments", "ver")), db: Session = Depends(get_db)):
    # mis cobros: los que registre y los que caja aplico a mis facturas (services/ownership.payment_scope)
    q = select(Payment).where(Payment.tenant_id == p.tenant.id, payment_scope(p))
    rows = db.scalars(q.order_by(Payment.id.desc()).limit(limit)).all()
    # el vinculo a la factura no venia en la respuesta (PaymentOut no traia invoice_id) y la columna Factura quedaba en "—"
    out = []
    for x in rows:
        inv = db.get(Invoice, x.invoice_id) if x.invoice_id else None
        item = PaymentOut.model_validate(x)
        item.invoice_number = inv.number if inv else None
        item.customer = _cust_name(db, inv.customer_id) if inv else None
        out.append(item)
    return out


# ---------- Grupos de facturacion ----------
@router.get("/billing-groups")
def billing_groups(p: Principal = Depends(require("settings", "ver")), db: Session = Depends(get_db)):
    rows = db.scalars(select(BillingGroup).where(BillingGroup.tenant_id == p.tenant.id)).all()
    return [
        {"id": g.id, "doc_type": g.doc_type, "prefix": g.prefix, "branch": g.branch, "terminal": g.terminal, "current": g.current, "is_default": g.is_default}
        for g in rows
    ]


# ---------- Dashboard ----------
def _sum_between(db: Session, col_model, col, tenant_id: int, start: date, end: date, extra=None) -> Decimal:
    stmt = select(func.coalesce(func.sum(col), 0)).where(col_model.tenant_id == tenant_id)
    if extra is not None:
        stmt = stmt.where(extra)
    return d(
        db.scalar(
            stmt.where(
                col_model.__table__.c[col.key if hasattr(col, "key") else "paid_at"] >= start,
                col_model.__table__.c[col.key if hasattr(col, "key") else "paid_at"] <= end,
            )
        )
    )


@router.get("/dashboard")
def dashboard(p: Principal = Depends(require("dashboard", "ver")), db: Session = Depends(get_db)):
    """Roles con reportes ven la empresa completa; el resto (p. ej. vendedor) solo lo que creo."""
    tid = p.tenant.id
    mine = not p.can("dashboard", "empresa")
    own_inv = doc_mine(Invoice, p.user.id) if mine else true()
    own_pay = (Payment.created_by == p.user.id) if mine else true()
    today = date.today()
    m0 = today.replace(day=1)
    pm_end = m0 - timedelta(days=1)
    pm0 = pm_end.replace(day=1)

    def pay_sum(a: date, b: date) -> Decimal:
        return d(
            db.scalar(
                select(func.coalesce(func.sum(Payment.amount), 0)).where(
                    Payment.tenant_id == tid, Payment.kind == "captura", Payment.status == "confirmado", Payment.paid_at >= a, Payment.paid_at <= b, own_pay
                )
            )
        )

    # facturas en la moneda de la empresa (cada una con el tipo de cambio que guardo): antes sumaba dolares y colones
    # como si fueran lo mismo. Igual que los reportes (services/reports._crc).
    base = p.tenant.default_currency or "CRC"
    en_moneda_empresa = case((Invoice.currency == base, Invoice.total), else_=Invoice.total * Invoice.fx_sell)

    def inv_sum(a: date, b: date) -> Decimal:
        return d(
            db.scalar(
                select(func.coalesce(func.sum(en_moneda_empresa), 0)).where(
                    Invoice.tenant_id == tid, Invoice.status != "anulada", Invoice.issue_date >= a, Invoice.issue_date <= b, own_inv
                )
            )
        )

    def pct(cur: Decimal, prev: Decimal) -> float | None:
        return None if prev == 0 else float((cur - prev) / prev * 100)

    show_cash = not mine or p.can("payments", "editar")  # vendedor: sin cobros; caja: sus cobros
    pagos = {"hoy": pay_sum(today, today), "mes": pay_sum(m0, today), "mes_anterior": pay_sum(pm0, pm_end)}
    fact = {"hoy": inv_sum(today, today), "mes": inv_sum(m0, today), "mes_anterior": inv_sum(pm0, pm_end)}
    pagos["variacion"] = pct(pagos["mes"], pagos["mes_anterior"])
    fact["variacion"] = pct(fact["mes"], fact["mes_anterior"])

    recent_pay = db.scalars(select(Payment).where(Payment.tenant_id == tid, own_pay).order_by(Payment.id.desc()).limit(6)).all()
    recent_inv = db.scalars(select(Invoice).where(Invoice.tenant_id == tid, own_inv).order_by(Invoice.id.desc()).limit(6)).all()
    # mismos filtros que la lista a la que lleva cada tarjeta (pending_where)
    pend = {k: pending_count(db, k, p) for k in PENDIENTES}
    if p.can("crm_pipeline", "ver"):
        from ..services.allies import cost_pending_count

        # misma definicion que Oportunidades -> Mis pendientes (services.allies.cost_pending_where)
        pend["costos_aliados"] = cost_pending_count(db, p)
    if p.can("sales", "ver"):
        from .store_checkout import pending_count as web_orders_pending

        # misma definicion que Pedidos de la tienda -> Por atender (services.store_orders.attention_where)
        pend["pedidos_web"] = web_orders_pending(db, p)
    if p.can("commissions", "aprobar"):
        from ..services.commissions import approvals_count

        pend["comisiones_por_aprobar"] = approvals_count(db, p)
    comisiones = None
    if p.can("commissions", "ver") and not p.can("commissions", "ver_todo") and p.ally_company_id is None:
        from ..services.commissions import summary_for_user

        # "va a poder ver cuanto esta ganando en tiempo real" (13/10 34:35): la misma cuenta que Mis comisiones
        r = summary_for_user(db, p.tenant, p.user.id, m0, today, p)
        comisiones = {"ganado_mes": r["ganado"]["total"], "pagado_mes": r["pagado"], "por_cobrar": r["por_cobrar"]["total"], "moneda": "CRC"}
    ceo = _ceo_row(db, p) if p.can("dashboard", "empresa") else None
    mi_embudo = None
    if mine and p.can("crm_pipeline", "ver"):
        from .pipeline import pipeline_summary  # la misma cuenta que el embudo: no pueden diferir

        mi_embudo = pipeline_summary(db, p)
    receivable = []
    if mine:
        for x in db.scalars(
            select(Invoice)
            .where(Invoice.tenant_id == tid, own_inv, Invoice.status.in_(("creado", "parcial", "vencida")), Invoice.balance > 0)
            .order_by(Invoice.due_date)
            .limit(8)
        ):
            receivable.append(
                {
                    "id": x.id,
                    "number": x.number,
                    "customer": _cust_name(db, x.customer_id),
                    "balance": x.balance,
                    "currency": x.currency,
                    "due_date": x.due_date,
                    "status": x.status,
                }
            )
    return {
        "scope": "mine" if mine else "company",
        "gerencia": ceo,
        "mi_embudo": mi_embudo,
        "por_cobrar": receivable,
        "pagos": pagos if show_cash else None,
        "facturado": fact,
        "pagos_recientes": [
            {
                "id": x.id,
                "invoice_id": x.invoice_id,
                "ref": x.external_ref,
                "method": x.method,
                "kind": x.kind,
                "amount": x.amount,
                "currency": x.currency,
                "date": x.paid_at,
                "status": x.status,
            }
            for x in (recent_pay if show_cash else [])
        ],
        "facturas_recientes": [
            {
                "id": x.id,
                "number": x.number,
                "customer": _cust_name(db, x.customer_id),
                "total": x.total,
                "balance": x.balance,
                "currency": x.currency,
                "status": x.status,
                "date": x.issue_date,
            }
            for x in recent_inv
        ],
        "acciones_pendientes": {
            **pend,
            "stock_bajo": len(invsvc.low_stock(db, tid)),
        },
        "comisiones": comisiones,
    }


def _ceo_row(db: Session, p: Principal) -> dict:
    """Fila de gerencia: en 30 segundos, cómo está Crimson. Pipeline, cobros, proyectos y trabajos de la semana."""
    from datetime import timedelta

    from ..models import CustomerAsset, Opportunity, Project, WorkOrder
    from ..routers.pipeline import OPEN_STATES
    from ..routers.projects import direct_cost, price_crc
    from ..services import pricing
    from ..services.archive import live

    tid = p.tenant.id
    today = date.today()
    m0 = today.replace(day=1)
    opps = db.scalars(select(Opportunity).where(Opportunity.tenant_id == tid, Opportunity.status.in_(OPEN_STATES), live(Opportunity))).all()
    pipeline = sum((d(o.amount) for o in opps), Decimal(0))
    weighted = sum((d(o.amount) * o.probability / 100 for o in opps), Decimal(0))
    receivable = d(
        db.scalar(select(func.coalesce(func.sum(Invoice.balance), 0)).where(Invoice.tenant_id == tid, Invoice.status.in_(("creado", "parcial", "vencida"))))
    )
    active = db.scalars(select(Project).where(Project.tenant_id == tid, Project.status.in_(("planificado", "en_curso", "pausado")), live(Project))).all()
    closed = db.scalars(
        select(Project).where(Project.tenant_id == tid, Project.status.in_(("terminado", "entregado", "facturado")), Project.updated_at >= m0, live(Project))
    ).all()
    # Utilidad y margen del mes son informacion de costos (Andres 08:07: solo el admin y lo que habilite en Ajustes):
    # sin catalog.costos ni se calculan y salen en None (el contador y solo lectura ven la fila sin utilidad).
    utilidad = None
    if p.sees_costs:
        # un cerrado sin costos registrados no es ganancia: queda fuera de la utilidad del mes hasta que se registren
        # misma cuenta que la ficha del proyecto (projects.economics): incluye aliados y la venta en colones
        costos = {x.id: direct_cost(db, x) for x in closed}
        sin_costos = [x for x in closed if costos[x.id] <= 0]
        closed_cost = [x for x in closed if costos[x.id] > 0]
        ventas = {x.id: price_crc(db, x) for x in closed_cost}
        profit = sum((ventas[x.id] - costos[x.id] for x in closed_cost), Decimal(0))
        venta = sum(ventas.values(), Decimal(0))
        utilidad = {
            "profit_month": profit.quantize(Decimal("0.01")),
            "margin_month": pricing.margin_of(venta, venta - profit),
            "projects_without_costs": len(sin_costos),
        }
    sold = d(
        db.scalar(select(func.coalesce(func.sum(Invoice.total), 0)).where(Invoice.tenant_id == tid, Invoice.status != "anulada", Invoice.issue_date >= m0))
    )
    week = today + timedelta(days=7 - today.weekday())
    jobs = db.scalars(select(WorkOrder).where(WorkOrder.tenant_id == tid, WorkOrder.status.in_(("asignada", "en_sitio", "en_proceso")), live(WorkOrder))).all()
    quotes_sent = db.scalar(select(func.count()).select_from(Quote).where(Quote.tenant_id == tid, Quote.status == "enviada"))
    quotes_won = db.scalar(select(func.count()).select_from(Quote).where(Quote.tenant_id == tid, Quote.status == "convertida", Quote.updated_at >= m0))
    warranties = [
        a
        for a in db.scalars(select(CustomerAsset).where(CustomerAsset.tenant_id == tid, CustomerAsset.warranty_until.is_not(None), live(CustomerAsset)))
        if a.warranty_until and 0 <= (a.warranty_until - today).days <= 45
    ]
    sop = None
    if p.can("support_desk", "ver"):
        from ..routers.support_desk import soporte_resumen

        sop = soporte_resumen(db, p)
    return {
        "pipeline": pipeline,
        "pipeline_weighted": weighted.quantize(Decimal("0.01")),
        "opportunities": len(opps),
        "receivable": receivable,
        "sold_month": sold,
        **(utilidad or {"profit_month": None, "margin_month": None, "projects_without_costs": None}),
        "projects_active": len(active),
        "projects_closed_month": len(closed),
        "jobs_open": len(jobs),
        "jobs_week": sum(1 for o in jobs if o.scheduled_at and o.scheduled_at.date() <= week),
        "jobs_late": sum(1 for o in jobs if o.scheduled_at and o.scheduled_at.date() < today),
        "quotes_sent": quotes_sent,
        "quotes_won_month": quotes_won,
        "warranties_soon": len(warranties),
        # misma definicion y alcance que la pantalla de tickets (support_desk.soporte_resumen)
        **({"tickets_open": sop["abiertos"], "tickets_late": sop["fuera_de_tiempo"]} if sop else {"tickets_open": None, "tickets_late": None}),
    }
