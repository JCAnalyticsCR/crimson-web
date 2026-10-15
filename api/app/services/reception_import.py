"""Importar el historico de recepciones de Fygaro: zip ("Download All", sueltos o zip de zips) o CSV/Excel.

- Se agrupa por clave: comprobante + MensajeReceptor (el que mando Fygaro) + respuestas de Hacienda + PDF.
- Con mensaje receptor: recepcion "historico" con la accion, condicion, actividad y montos de ese mensaje.
- Sin mensaje: "por_revisar" si sigue en plazo, o "historico" con aviso "sin mensaje: se presume aceptado".
- Si la clave ya existe y solo tiene mensajes simulado/pruebas (se respondio aqui durante la convivencia), GANA Fygaro.
- Las historicas nunca se envian a Hacienda ni se reabren.
- NO toca los consecutivos F05/F06/F07: guarda el mayor NumeroConsecutivoReceptor de Fygaro en
  tenant.settings["fygaro_import"]["max_receptor"]; el admin lo pone en Ajustes -> Hacienda -> Consecutivos.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..core.deps import Principal
from ..models import ExpenseCategory, ReceivedDocument, ReceptionResponse, Tenant
from ..providers.hacienda import xml44
from . import reception as rsvc
from . import tabular as tb
from .bitacora import audit_change, snapshot
from .reception_calc import ACTION_OF_CODE, IVA_NAMES, deadline, name_of
from .reception_intake import CLAVE_RE, CONSEC_RE, ZipRejected, apply_supplier_response, expand_zip, initial_status, sniff, supplier_response_problem
from .sequences import ensure_fiscal_groups

MAX_IMPORT = 50 * 1024 * 1024  # zip de XML (expand_zip pone sus propios topes)
# Excel o CSV: el mismo tope de archivo que el resto de importaciones de tabla. Las filas, mas holgadas que las 5000 de
# /imports: el historico de comprobantes recibidos de un ano de Fygaro puede pasarlas.
MAX_TABLE = 5 * 1024 * 1024
MAX_TABLE_ROWS = 20_000
TEMPLATE = [
    "Clave",
    "Fecha",
    "Tipo",
    "Emisor",
    "Identificacion emisor",
    "Moneda",
    "Tipo de cambio",
    "Impuesto",
    "Total",
    "Accion",
    "Condicion IVA",
    "Codigo actividad",
    "IVA acreditado",
    "Gasto aplicable",
    "Consecutivo receptor",
    "Categoria",
]
COLS = {
    "clave": ("clave", "clave_numerica", "clave_del_comprobante"),
    "date": ("fecha", "fecha_emision", "fecha_de_emision"),
    "doc_type": ("tipo", "tipo_documento", "tipo_de_documento"),
    "issuer": ("emisor", "proveedor", "nombre_emisor", "nombre_proveedor"),
    "issuer_id": ("identificacion_emisor", "cedula_emisor", "identificacion", "cedula", "cedula_proveedor"),
    "currency": ("moneda", "divisa"),
    "fx": ("tipo_de_cambio", "tipo_cambio", "tc"),
    "tax": ("impuesto", "iva", "total_impuesto"),
    "total": ("total", "total_comprobante", "monto_total"),
    "action": ("accion", "respuesta", "mensaje", "estado_respuesta"),
    "condition": ("condicion_iva", "condicion_impuesto", "condicion_del_iva"),
    "activity": ("codigo_actividad", "actividad", "codigo_de_actividad"),
    "credit": ("iva_acreditado", "iva_acreditable", "monto_impuesto_acreditar"),
    "applicable": ("gasto_aplicable", "monto_gasto_aplicable"),
    "consecutive": ("consecutivo_receptor", "numero_consecutivo_receptor"),
    "category": ("categoria", "categoria_gasto"),
}
TYPE_NAMES = {"fe": "FE", "factura": "FE", "factura_electronica": "FE", "nc": "NC", "nota_de_credito": "NC", "nd": "ND", "nota_de_debito": "ND", "te": "TE", "tiquete": "TE"}
MR_NAME_HINTS = ("receptor", "recepcion", "aceptacion", "mr")


@dataclass
class Group:
    clave: str
    doc: tuple | None = None  # (name, data, root)
    mr: tuple | None = None
    mh: list = field(default_factory=list)  # [(name, data, root)]
    pdfs: list = field(default_factory=list)


def is_xlsx(data: bytes) -> bool:
    return data[:2] == b"PK" and b"[Content_Types].xml" in data[:4096]


def _action(v) -> str | None:
    s = tb.norm(v)
    if not s:
        return None
    if s in ("1", "aceptado", "aceptada", "aceptacion", "total", "aceptacion_total"):
        return "aceptada"
    if s in ("2", "parcial", "aceptado_parcialmente", "aceptacion_parcial", "aceptada_parcialmente"):
        return "parcial"
    if s in ("3", "rechazado", "rechazada", "rechazo"):
        return "rechazada"
    return None


def _cond(v) -> str:
    s = tb.norm(v)
    if not s:
        return "credito"
    s = s.zfill(2) if s.isdigit() else s
    try:
        return name_of(s)
    except Exception:  # noqa: BLE001
        aliases = {"no_genera_credito": "no_credito", "gasto": "no_credito", "proporcionalidad": "proporcional", "general": "credito"}
        return aliases.get(s, "credito")


def _track_max(maxes: dict[str, int], consecutive: str | None, groups: dict) -> None:
    if not consecutive or not re.fullmatch(r"\d{20}", consecutive):
        return
    code = consecutive[8:10]
    g = groups.get(f"F{code}")
    if code not in ("05", "06", "07") or g is None:
        return
    if consecutive[:3] != g.branch or consecutive[3:8] != g.terminal:
        return  # otra sucursal/terminal: no es la numeracion que sigue Crimson
    n = int(consecutive[10:])
    maxes[code] = max(maxes.get(code, 0), n)


def _mh_is_for_our_mr(name: str, root) -> bool:
    low = name.lower()
    base = re.split(r"[\\/]", low)[-1]
    if any(h in base for h in MR_NAME_HINTS if h != "mr") or re.search(r"(^|[^a-z])mr([^a-z]|$)", base):
        return True
    det = (xml44.text(root, "DetalleMensaje") or "").lower()
    return "receptor" in det or "confirmaci" in det


def _historic_response(db: Session, tenant: Tenant, doc: ReceivedDocument, mr: dict, xml_text: str | None, hacienda: tuple | None, user_id: int) -> ReceptionResponse:
    from .hacienda_sync import close_superseded
    from .reception_calc import IVA_CODES

    # gana Fygaro: lo de simulado/pruebas queda en la historia, pero lo que seguia por enviar o en consulta se cierra (si
    # no, el worker lo mandaria o lo consultaria despues y su resultado pisaria el estado "historico" y daria alertas)
    close_superseded(db, doc.id, "Reemplazado por el mensaje de Fygaro (histórico)")
    attempt = (db.scalar(select(func.max(ReceptionResponse.attempt)).where(ReceptionResponse.received_document_id == doc.id)) or 0) + 1
    action = ACTION_OF_CODE.get(mr["mensaje"] or "1", "aceptada")
    cond = IVA_NAMES.get(mr["condition"] or "", None) if action != "rechazada" else None
    r = ReceptionResponse(
        tenant_id=tenant.id,
        received_document_id=doc.id,
        attempt=attempt,
        message_code=mr["mensaje"] or "1",
        iva_condition_code=IVA_CODES.get(cond) if cond else None,
        activity_code=mr["activity_code"],
        tax_total=mr["tax_total"] or doc.tax_total,
        total=mr["total"] or doc.total,
        credit_amount=mr["credit"],
        applicable_amount=mr["applicable"],
        detail=mr["detalle"],
        consecutive=mr["consecutive"] or f"H{doc.id:019d}"[:20],
        mode="produccion",
        emitted_at=mr["fecha"] or datetime.now(UTC),
        xml_signed=xml_text,
        status="historico",
        hacienda_xml=hacienda[0] if hacienda else None,
        hacienda_detail=hacienda[1] if hacienda else None,
        created_by=user_id,
        created_at=datetime.now(UTC),
    )
    db.add(r)
    db.flush()
    # la recepcion toma los datos del mensaje de Fygaro (el que valio en produccion)
    iva = Decimal(str(doc.iva_total if doc.iva_total is not None else doc.tax_total or 0))
    if cond and mr["credit"] is None:
        r.credit_amount = {"credito": iva, "bienes_capital": iva, "no_credito": Decimal(0)}.get(cond)
    if cond and mr["applicable"] is None and r.credit_amount is not None and action == "aceptada":
        r.applicable_amount = Decimal(str(doc.total or 0)) - Decimal(str(r.credit_amount))
    doc.action = action
    doc.iva_condition = cond or doc.iva_condition
    doc.activity_code = mr["activity_code"]
    doc.credit_amount = r.credit_amount
    doc.applicable_amount = r.applicable_amount
    doc.response_detail = mr["detalle"]
    doc.status = "historico"
    doc.hacienda_status = "historico"
    doc.responded_at = mr["fecha"] or datetime.now(UTC)
    return r


def _maybe_expense(db: Session, p: Principal, doc: ReceivedDocument, category_id: int | None) -> bool:
    if doc.expense_id or doc.action not in ("aceptada", "parcial"):
        return False
    try:
        amounts = rsvc.amounts_from_response(doc)
    except HTTPException:
        return False  # parcial sin monto aceptado conocido: sin gasto automatico (nunca uno en cero)
    rsvc.create_expense_from_document(db, p.tenant.id, doc, p.user.id, category_id, amounts, ip=p.ip)
    return True


def _update_linked_after_fygaro(db: Session, p: Principal, doc: ReceivedDocument) -> None:
    """Como en retry: el gasto ligado toma los montos del mensaje que valio (y se anula si fue rechazo)."""
    if not doc.expense_id:
        return
    from ..models import Expense

    e = db.get(Expense, doc.expense_id)
    if not e:
        return
    antes = snapshot(e)
    if doc.action == "rechazada":
        e.status = "anulado"
    else:
        try:
            amounts = rsvc.amounts_from_response(doc)
        except HTTPException:
            amounts = {}  # parcial sin monto aceptado conocido: el gasto conserva sus montos (nunca en cero)
        for k, v in amounts.items():
            setattr(e, k, v)
    audit_change(db, p, "update", "expense", e.id, antes, snapshot(e), reason=f"Mensaje de Fygaro importado para {doc.clave}")


def _category(db: Session, tid: int, name, cache: dict, commit: bool) -> int | None:
    name = tb.text(name, 120)
    if not name:
        return None
    key = name.lower()
    if key not in cache:
        c = db.scalar(select(ExpenseCategory).where(ExpenseCategory.tenant_id == tid, func.lower(ExpenseCategory.name) == key))
        if c is None and commit:
            c = ExpenseCategory(tenant_id=tid, name=name)
            db.add(c)
            db.flush()
        cache[key] = c.id if c else None
    return cache[key]


def import_zip(db: Session, p: Principal, data: bytes, commit: bool, create_expenses: bool) -> tuple[list[dict], dict[str, int]]:
    tenant = p.tenant
    try:
        files = expand_zip(data, max_total=MAX_IMPORT)
    except ZipRejected as e:
        raise HTTPException(422, f"No se pudo leer el zip: {e}") from e
    groups: dict[str, Group] = {}
    loose_pdfs: list[tuple[str, bytes]] = []
    result: list[dict] = []
    for name, blob in files:
        kind = sniff(blob)
        if kind == "pdf":
            loose_pdfs.append((name, blob))
            continue
        if kind != "xml":
            continue
        try:
            root = xml44.parse_xml_bytes(blob)
        except xml44.XmlRejected as e:
            result.append({"row": len(result) + 1, "action": "error", "detail": f"{name}: {e}"})
            continue
        cls = xml44.classify(root)
        cl = xml44.text(root, "Clave")
        if not cl:
            continue
        g = groups.setdefault(cl, Group(cl))
        if cls == "comprobante":
            g.doc = (name, blob, root)
        elif cls == "mensaje_receptor":
            g.mr = (name, blob, root)
        elif cls == "mensaje_hacienda":
            g.mh.append((name, blob, root))
    for name, blob in loose_pdfs:
        nums = set(CLAVE_RE.findall(name)) | set(CONSEC_RE.findall(name))
        for g in groups.values():
            cons = xml44.text(g.doc[2], "NumeroConsecutivo") if g.doc else None
            if g.clave in nums or (cons and cons in nums):
                g.pdfs.append((name, blob))
                break
    fgroups = ensure_fiscal_groups(db, tenant.id)
    maxes: dict[str, int] = {}
    from ..routers.media import save_media

    for i, g in enumerate(sorted(groups.values(), key=lambda g: g.clave), start=1):
        mr = xml44.parse_mensaje_receptor(g.mr[2]) if g.mr else None
        if mr:
            _track_max(maxes, mr["consecutive"], fgroups)
        # respuestas de Hacienda: a nuestro mensaje o al proveedor
        ours, supplier = None, None
        for name, blob, root in g.mh:
            if _mh_is_for_our_mr(name, root) and ours is None:
                ours = (name, blob, root)
            elif supplier is None:
                supplier = (name, blob, root)
            else:
                ours = ours or (name, blob, root)
        hacienda = (xml44.to_utf8_text(ours[1], ours[2]), (xml44.text(ours[2], "DetalleMensaje") or "")[:500] or None) if ours else None
        existing = db.scalar(select(ReceivedDocument).where(ReceivedDocument.tenant_id == tenant.id, ReceivedDocument.clave == g.clave))
        try:
            # cada grupo en su propio savepoint (el with lo confirma aun con "continue"): un choque del unico
            # (consecutivo, clave) es un error de esa fila, no un 500 que tumba toda la importacion
            with db.begin_nested():
                if existing is not None:
                    changed = []
                    if supplier and not existing.xml_supplier_response and not supplier_response_problem(existing, supplier[2]):
                        if commit:
                            apply_supplier_response(db, existing, supplier[2], xml44.to_utf8_text(supplier[1], supplier[2]))
                        changed.append("respuesta del proveedor")
                    if g.pdfs and not existing.pdf_media_id:
                        if commit:
                            m, _ = save_media(db, tenant.id, g.pdfs[0][1], g.pdfs[0][0], p.user.id, visibility="private", module="accounting")
                            existing.pdf_media_id = m.id
                        changed.append("PDF")
                    rs = rsvc.responses(db, existing)
                    only_local = all(r.mode in ("simulado", "pruebas") and r.status != "historico" for r in rs)
                    if mr and only_local and existing.status != "historico":
                        if commit:
                            _historic_response(db, tenant, existing, mr, xml44.to_utf8_text(g.mr[1], g.mr[2]), hacienda, p.user.id)
                            existing.origin = existing.origin or "historico"
                            _update_linked_after_fygaro(db, p, existing)
                        changed.append("mensaje de Fygaro (gana sobre lo respondido aquí)")
                    if changed:
                        result.append({"row": i, "action": "actualizar", "detail": f"{g.clave}: se agrega " + ", ".join(changed)})
                    else:
                        result.append({"row": i, "action": "omitir", "detail": f"{g.clave}: ya registrada"})
                    continue
                if not g.doc:
                    result.append({"row": i, "action": "omitir", "detail": f"{g.clave}: mensaje sin comprobante en el archivo"})
                    continue
                parsed = xml44.parse_comprobante(g.doc[2], g.doc[1])
                st, reason = initial_status(tenant, parsed)
                if st is None:
                    result.append({"row": i, "action": "omitir", "detail": f"{g.clave}: {reason}"})
                    continue
                detail = parsed.issuer_name or g.clave
                if mr:
                    final = "historico"
                elif st == "por_revisar" and parsed.issue_date and (deadline(parsed.issue_date) or date.min) >= date.today():
                    final = "por_revisar"
                elif st == "por_revisar":
                    final, detail = "historico", f"{detail} · sin mensaje: se presume aceptado"
                else:
                    final = st
                result.append({"row": i, "action": "nuevo", "detail": f"{g.clave} · {detail} · {final}"})
                if not commit:
                    continue
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
                    receiver_ok=xml44.same_id(parsed.receiver_id, tenant.tax_id) if parsed.receiver_id else None,
                    reference_clave=parsed.reference_clave,
                    status=final if final != "historico" else "por_revisar",
                    origin="historico",
                    xml_document=xml44.to_utf8_text(g.doc[1], g.doc[2]),
                    hacienda_status="sin_respuesta",
                    iva_condition="credito",
                    deadline=deadline(parsed.issue_date),
                )
                db.add(doc)
                db.flush()
                if supplier:
                    apply_supplier_response(db, doc, supplier[2], xml44.to_utf8_text(supplier[1], supplier[2]))
                if g.pdfs:
                    m, _ = save_media(db, tenant.id, g.pdfs[0][1], g.pdfs[0][0], p.user.id, visibility="private", module="accounting")
                    doc.pdf_media_id = m.id
                if mr:
                    _historic_response(db, tenant, doc, mr, xml44.to_utf8_text(g.mr[1], g.mr[2]), hacienda, p.user.id)
                    if create_expenses:
                        _maybe_expense(db, p, doc, None)
                elif final == "historico":
                    doc.status = "historico"
                    doc.hacienda_status = "historico"
                db.flush()
        except HTTPException as e:
            _drop_last_ok(result, i)
            result.append({"row": i, "action": "error", "detail": f"{g.clave}: {e.detail}"})
        except xml44.XmlRejected as e:
            _drop_last_ok(result, i)
            result.append({"row": i, "action": "error", "detail": f"{g.clave}: {e}"})
        except IntegrityError as e:
            _drop_last_ok(result, i)
            result.append({"row": i, "action": "error", "detail": f"{g.clave}: {_integrity_detail(e)}"})
    return result, maxes


def _drop_last_ok(result: list[dict], row: int) -> None:
    """La fila ya se habia anotado como nuevo/actualizar antes de fallar: queda solo el error."""
    if result and result[-1]["row"] == row and result[-1]["action"] != "error":
        result.pop()


def _integrity_detail(e: IntegrityError) -> str:
    if "consecutive" in str(e.orig if hasattr(e, "orig") else e):
        return "el consecutivo del mensaje receptor ya existe (otro mensaje de producción con el mismo número)"
    return "ya existe un registro con esos datos"


def import_table(db: Session, p: Principal, data: bytes, filename: str, commit: bool, create_expenses: bool) -> tuple[list[dict], dict[str, int], dict]:
    tenant = p.tenant
    if len(data) > MAX_TABLE:
        raise HTTPException(413, "Archivo muy grande (máximo 5 MB para Excel o CSV; los XML pueden ir en un .zip de hasta 50 MB)")
    try:
        rows = tb.read_table(data, filename, max_rows=MAX_TABLE_ROWS)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"No se pudo leer el archivo: {e}") from e
    if not rows:
        raise HTTPException(422, "El archivo no tiene filas")
    headers = sorted({h for r in rows for h in r})
    recognized = {f: next((a for a in aliases if a in headers), None) for f, aliases in COLS.items()}
    fgroups = ensure_fiscal_groups(db, tenant.id)
    maxes: dict[str, int] = {}
    cats: dict = {}
    result = []
    seen = set()
    for i, r in enumerate(rows, start=2):
        v = {k: tb.pick(r, *aliases) for k, aliases in COLS.items()}
        cl = re.sub(r"\D", "", str(v["clave"] or ""))
        if len(cl) != 50:
            result.append({"row": i, "action": "error", "detail": "Clave de 50 dígitos requerida"})
            continue
        if cl in seen:
            result.append({"row": i, "action": "omitir", "detail": f"{cl}: repetida en el archivo"})
            continue
        seen.add(cl)
        cons = re.sub(r"\D", "", str(v["consecutive"] or "")) or None
        _track_max(maxes, cons, fgroups)
        if db.scalar(select(ReceivedDocument.id).where(ReceivedDocument.tenant_id == tenant.id, ReceivedDocument.clave == cl)):
            result.append({"row": i, "action": "omitir", "detail": f"{cl}: ya registrada"})
            continue
        action = _action(v["action"]) or "aceptada"
        cond = _cond(v["condition"])
        total = tb.num(v["total"]) or Decimal(0)
        tax = tb.num(v["tax"]) or Decimal(0)
        dt = tb.to_date(v["date"])
        result.append({"row": i, "action": "nuevo", "detail": f"{cl} · {tb.text(v['issuer'], 80) or ''} · {action}"})
        if not commit:
            continue
        cat_id = _category(db, tenant.id, v["category"], cats, commit) if create_expenses else None  # fuera del savepoint
        try:
            with db.begin_nested():  # una fila = un savepoint: un choque del unico (p. ej. el consecutivo) es error de esa fila
                _table_row(db, p, tenant, v, cl, cons, action, cond, total, tax, dt, cat_id, create_expenses)
        except IntegrityError as e:
            _drop_last_ok(result, i)
            result.append({"row": i, "action": "error", "detail": f"{cl}: {_integrity_detail(e)}"})
    return result, maxes, recognized


def _table_row(
    db: Session,
    p: Principal,
    tenant: Tenant,
    v: dict,
    cl: str,
    cons: str | None,
    action: str,
    cond: str,
    total: Decimal,
    tax: Decimal,
    dt,
    cat_id: int | None,
    create_expenses: bool,
) -> None:
    doc_type = TYPE_NAMES.get(tb.norm(v["doc_type"]), "FE")
    credit = tb.num(v["credit"])
    applicable = tb.num(v["applicable"])
    if credit is None and action != "rechazada":
        credit = {"credito": tax, "bienes_capital": tax, "no_credito": Decimal(0)}.get(cond)
    if applicable is None and credit is not None and action == "aceptada":
        applicable = total - credit
    doc = ReceivedDocument(
        tenant_id=tenant.id,
        clave=cl,
        consecutive=cl[21:41],
        issuer_name=tb.text(v["issuer"], 200),
        issuer_id=xml44.norm_id(tb.text(v["issuer_id"], 30)),
        issue_date=dt,
        currency="USD" if tb.norm(v["currency"]) in ("usd", "dolares", "dolar") else "CRC",
        fx_rate=tb.num(v["fx"]) or Decimal(1),
        subtotal=total - tax,
        tax_total=tax,
        total=total,
        doc_type=doc_type,
        status="historico",
        origin="historico",
        action=action,
        iva_condition=cond if action != "rechazada" else "credito",
        activity_code=tb.text(v["activity"], 10),
        credit_amount=credit if action != "rechazada" else None,
        applicable_amount=applicable if action != "rechazada" else None,
        hacienda_status="historico",
        deadline=deadline(dt),
        responded_at=datetime.now(UTC),
    )
    db.add(doc)
    db.flush()
    if cons:
        from .reception_calc import IVA_CODES, MESSAGE_CODE

        db.add(
            ReceptionResponse(
                tenant_id=tenant.id,
                received_document_id=doc.id,
                attempt=1,
                message_code=MESSAGE_CODE[action],
                iva_condition_code=IVA_CODES.get(cond) if action != "rechazada" else None,
                activity_code=doc.activity_code,
                tax_total=tax,
                total=total,
                credit_amount=doc.credit_amount,
                applicable_amount=doc.applicable_amount,
                consecutive=cons[:20],
                mode="produccion",
                emitted_at=datetime.now(UTC),
                status="historico",
                created_by=p.user.id,
                created_at=datetime.now(UTC),
            )
        )
        db.flush()
    if create_expenses:
        _maybe_expense(db, p, doc, cat_id)
    db.flush()


def run(db: Session, p: Principal, data: bytes, filename: str, commit: bool, create_expenses: bool) -> dict:
    if len(data) > MAX_IMPORT:
        raise HTTPException(413, "Archivo muy grande (máximo 50 MB)")
    if sniff(data) == "zip" and not is_xlsx(data):
        result, maxes = import_zip(db, p, data, commit, create_expenses)
        recognized: dict = {}
        rows = len(result)
    elif sniff(data) == "xml":
        result, maxes = import_zip(db, p, _zip_one(filename, data), commit, create_expenses)
        recognized, rows = {}, len(result)
    else:
        result, maxes, recognized = import_table(db, p, data, filename, commit, create_expenses)
        rows = len(result)
    notes = []
    if maxes:
        st = dict(p.tenant.settings or {})
        fy = dict(st.get("fygaro_import") or {})
        prev = {k: int(v) for k, v in (fy.get("max_receptor") or {}).items()}
        merged = {k: max(prev.get(k, 0), maxes.get(k, 0)) for k in set(prev) | set(maxes)}
        for code, n in sorted(maxes.items()):
            label = {"05": "aceptación", "06": "aceptación parcial", "07": "rechazo"}[code]
            notes.append(f"El último consecutivo de {label} en el archivo es {n}: el administrador debe ponerlo en Ajustes → Hacienda → Consecutivos")
        if commit:
            fy["max_receptor"] = merged
            st["fygaro_import"] = fy
            p.tenant.settings = st
            flag_modified(p.tenant, "settings")
    return {"result": result, "rows": rows, "columns": recognized, "notes": notes, "max_receptor": maxes}


def _zip_one(name: str, data: bytes) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name or "comprobante.xml", data)
    return buf.getvalue()
