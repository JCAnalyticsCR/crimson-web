"""QuickBooks Online: importar plan de cuentas, mapear roles, revisar mes, exportar.

Permisos:
  GET  /qb/*          → accounting.ver
  POST /qb/accounts   → accounting.crear
  PUT  /qb/mapping    → accounting.editar
  POST /qb/*/export   → accounting.exportar
  POST /qb/*/reopen   → admin (reabrir mes cerrado)
"""

from __future__ import annotations

import csv
import io
import re
import uuid
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import (
    ACCOUNTING_ROLES,
    AccountingExport,
    AccountingPeriod,
    AccountingRoleMapping,
    BankAccount,
    Expense,
    ExpenseCategory,
    Invoice,
    JournalEntry,
    QboAccount,
    Tenant,
    bank_role,
    expense_cat_role,
)
from ..services import journal as js

# Formatos de fecha soportados para la importación en QBO
QBO_DATE_FORMATS = {
    "dd/mm/aaaa": "%d/%m/%Y",    # predeterminado (empresas en español)
    "mm/dd/aaaa": "%m/%d/%Y",    # empresas con QBO en inglés (US)
    "aaaa-mm-dd": "%Y-%m-%d",    # ISO 8601
}
_DEFAULT_DATE_FMT = "dd/mm/aaaa"

router = APIRouter(prefix="/qb", tags=["quickbooks"])

# Límite de filas por archivo de importación QBO (configurable; default QBO ≈ 1000 filas)
QBO_ROWS_PER_FILE = 1000


# ---------------------------------------------------------------------------
# Importar plan de cuentas
# ---------------------------------------------------------------------------

def _parse_accounts(content: bytes, filename: str) -> list[dict]:
    """Parsea el Excel/CSV de QBO 'Lista de cuentas'.

    Columnas esperadas (en español o inglés, tolera cabeceras extra y filas de totales):
      Número / Number, Cuenta / Account, Tipo / Type, Tipo de detalle / Detail type,
      Descripción / Description, Moneda / Currency
    """
    text = content.decode("utf-8-sig", errors="replace")
    dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)

    # Normalizar nombres de columnas
    HEADERS: dict[str, str] = {
        # español
        "cuenta": "full_name", "número": "number", "numero": "number",
        "tipo": "account_type", "tipo de detalle": "detail_type",
        "descripción": "description", "descripcion": "description",
        "moneda": "currency",
        # inglés
        "account": "full_name", "number": "number",
        "type": "account_type", "detail type": "detail_type",
        "description": "description", "currency": "currency",
        # variantes
        "account number": "number", "account type": "account_type",
        "account name": "full_name", "nombre": "full_name",
    }

    rows = []
    for raw in reader:
        row: dict[str, str] = {}
        for key, val in raw.items():
            if key is None:
                continue
            norm = key.strip().lower()
            mapped = HEADERS.get(norm)
            if mapped and val and str(val).strip():
                row[mapped] = str(val).strip()
        if not row.get("full_name"):
            continue
        # Filtrar filas de totales (contienen "Total" en la cuenta)
        if re.match(r"^total", row["full_name"], re.IGNORECASE):
            continue
        rows.append(row)
    return rows


@router.post("/accounts/import", status_code=201)
async def import_accounts(
    file: UploadFile = File(...),
    p: Principal = Depends(require("accounting", "crear")),
    db: Session = Depends(get_db),
):
    """Sube el CSV/Excel exportado de QBO 'Lista de cuentas'.

    Reimportable: upserta por full_name, no borra las que ya tienen mapeo.
    Las cuentas que no aparecen en este lote se marcan active=False.
    """
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(413, "Archivo demasiado grande (máx 5 MB)")

    # Soporte XLSX: convertir a CSV plano
    if file.filename and file.filename.lower().endswith(".xlsx"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            ws = wb.active
            buf = io.StringIO()
            w = csv.writer(buf)
            for row in ws.iter_rows(values_only=True):
                w.writerow([str(c) if c is not None else "" for c in row])
            content = buf.getvalue().encode("utf-8")
        except Exception as exc:
            raise HTTPException(422, f"No se pudo leer el XLSX: {exc}") from exc

    rows = _parse_accounts(content, file.filename or "")
    if not rows:
        raise HTTPException(422, "No se encontraron cuentas en el archivo. "
                            "Verifique que sea el reporte 'Lista de cuentas' de QBO.")

    batch = str(uuid.uuid4())[:8]
    tid = p.tenant.id

    # Upsert
    existing_names = {
        r.full_name: r
        for r in db.scalars(select(QboAccount).where(QboAccount.tenant_id == tid))
    }

    seen: set[str] = set()
    inserted = 0
    updated = 0
    for row in rows:
        name = row["full_name"]
        if name in seen:
            continue
        seen.add(name)
        if name in existing_names:
            acc = existing_names[name]
            acc.number = row.get("number") or acc.number
            acc.account_type = row.get("account_type") or acc.account_type
            acc.detail_type = row.get("detail_type") or acc.detail_type
            acc.description = row.get("description") or acc.description
            acc.currency = row.get("currency", "CRC")
            acc.active = True
            acc.import_batch = batch
            updated += 1
        else:
            acc = QboAccount(
                tenant_id=tid,
                full_name=name,
                number=row.get("number"),
                account_type=row.get("account_type"),
                detail_type=row.get("detail_type"),
                description=row.get("description"),
                currency=row.get("currency", "CRC"),
                active=True,
                import_batch=batch,
            )
            db.add(acc)
            inserted += 1

    # Marcar inactivas las que no aparecieron en este lote
    deactivated = 0
    for name, acc in existing_names.items():
        if name not in seen and acc.active:
            # Solo desactivar si no tiene mapeo activo
            has_mapping = db.scalar(
                select(AccountingRoleMapping).where(
                    AccountingRoleMapping.tenant_id == tid,
                    AccountingRoleMapping.qbo_account_id == acc.id,
                )
            )
            if not has_mapping:
                acc.active = False
                deactivated += 1

    db.commit()
    return {"inserted": inserted, "updated": updated, "deactivated": deactivated, "batch": batch}


@router.get("/accounts")
def list_accounts(p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    accs = db.scalars(
        select(QboAccount).where(QboAccount.tenant_id == p.tenant.id, QboAccount.active.is_(True))
        .order_by(QboAccount.number.nulls_last(), QboAccount.full_name)
    ).all()
    return [
        {"id": a.id, "full_name": a.full_name, "number": a.number,
         "account_type": a.account_type, "currency": a.currency}
        for a in accs
    ]


# ---------------------------------------------------------------------------
# Mapeo de roles
# ---------------------------------------------------------------------------

class MappingIn(BaseModel):
    role_key: str
    qbo_account_id: int | None = None


@router.get("/mapping")
def get_mapping(p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    """Devuelve el catálogo completo de roles con su cuenta QBO asignada (o null)."""
    tid = p.tenant.id

    # Cuentas bancarias dinámicas
    bank_accounts = db.scalars(
        select(BankAccount).where(BankAccount.tenant_id == tid, BankAccount.active.is_(True))
    ).all()

    # Categorías de gasto dinámicas
    categories = db.scalars(
        select(ExpenseCategory).where(ExpenseCategory.tenant_id == tid, ExpenseCategory.active.is_(True))
    ).all()

    # Mapeos guardados
    saved = {
        m.role_key: m
        for m in db.scalars(
            select(AccountingRoleMapping).where(AccountingRoleMapping.tenant_id == tid)
        )
    }

    def _role_entry(key: str, label: str) -> dict:
        m = saved.get(key)
        return {
            "role_key": key,
            "label": label,
            "qbo_account_id": m.qbo_account_id if m else None,
            "qbo_account_name": m.account.full_name if (m and m.account) else None,
        }

    roles = []
    for key, label in ACCOUNTING_ROLES:
        roles.append(_role_entry(key, label))
    for ba in bank_accounts:
        roles.append(_role_entry(bank_role(ba.id), f"Cuenta bancaria: {ba.name}"))
    for cat in categories:
        roles.append(_role_entry(expense_cat_role(cat.id), f"Gasto: {cat.name}"))

    unmapped = sum(1 for r in roles if r["qbo_account_id"] is None)
    return {"roles": roles, "unmapped": unmapped}


@router.put("/mapping")
def save_mapping(
    items: list[MappingIn],
    p: Principal = Depends(require("accounting", "editar")),
    db: Session = Depends(get_db),
):
    tid = p.tenant.id
    saved = {
        m.role_key: m
        for m in db.scalars(
            select(AccountingRoleMapping).where(AccountingRoleMapping.tenant_id == tid)
        )
    }
    now = datetime.now(UTC)
    for item in items:
        if item.role_key in saved:
            saved[item.role_key].qbo_account_id = item.qbo_account_id
            saved[item.role_key].updated_by = p.user.id
            saved[item.role_key].updated_at = now
        else:
            db.add(AccountingRoleMapping(
                tenant_id=tid,
                role_key=item.role_key,
                qbo_account_id=item.qbo_account_id,
                updated_by=p.user.id,
                updated_at=now,
            ))
    db.commit()
    return {"ok": True, "updated": len(items)}


# ---------------------------------------------------------------------------
# Períodos y revisión de mes
# ---------------------------------------------------------------------------

def _get_or_create_period(db: Session, tenant_id: int, month: str) -> AccountingPeriod:
    p = db.scalar(
        select(AccountingPeriod).where(
            AccountingPeriod.tenant_id == tenant_id,
            AccountingPeriod.month == month,
        )
    )
    if not p:
        p = AccountingPeriod(tenant_id=tenant_id, month=month, status="abierto")
        db.add(p)
        db.flush()
    return p


@router.get("/periods")
def list_periods(p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(AccountingPeriod).where(AccountingPeriod.tenant_id == p.tenant.id)
        .order_by(AccountingPeriod.month.desc())
    ).all()
    return [
        {"month": r.month, "status": r.status,
         "exported_at": r.exported_at.isoformat() if r.exported_at else None}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Configuración QBO de la empresa (fecha + opciones futuras)
# ---------------------------------------------------------------------------

class QbConfigIn(BaseModel):
    date_format: str = _DEFAULT_DATE_FMT   # "dd/mm/aaaa" | "mm/dd/aaaa" | "aaaa-mm-dd"


@router.get("/config")
def get_config(p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    """Devuelve la configuración QBO de la empresa (formato de fecha, etc.)."""
    t = db.get(Tenant, p.tenant.id)
    settings = t.settings or {} if t else {}
    return {
        "date_format": settings.get("qbo_date_format", _DEFAULT_DATE_FMT),
        "date_format_options": list(QBO_DATE_FORMATS.keys()),
    }


@router.put("/config")
def save_config(
    data: QbConfigIn,
    p: Principal = Depends(require("accounting", "editar")),
    db: Session = Depends(get_db),
):
    """Guarda la configuración QBO en tenant.settings."""
    if data.date_format not in QBO_DATE_FORMATS:
        raise HTTPException(422, f"Formato de fecha inválido. Opciones: {list(QBO_DATE_FORMATS.keys())}")
    t = db.get(Tenant, p.tenant.id)
    if t is None:
        raise HTTPException(404, "Empresa no encontrada")
    settings = dict(t.settings or {})
    settings["qbo_date_format"] = data.date_format
    t.settings = settings
    db.commit()
    return {"ok": True, "date_format": data.date_format}


@router.get("/review/{month}")
def review_month(
    month: str,
    p: Principal = Depends(require("accounting", "ver")),
    db: Session = Depends(get_db),
):
    """Lista de problemas que bloquean o advierten antes de exportar."""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(422, "Mes inválido (AAAA-MM)")

    # Regenerar asientos del mes
    result = js.regenerate_all(db, p.tenant.id, month)

    tid = p.tenant.id
    issues: list[dict] = []

    # 1. Roles sin mapear usados en el mes
    mapping = {
        m.role_key: m.qbo_account_id
        for m in db.scalars(
            select(AccountingRoleMapping).where(AccountingRoleMapping.tenant_id == tid)
        )
    }
    used_roles: set[str] = set()
    entries = db.scalars(
        select(JournalEntry).where(
            JournalEntry.tenant_id == tid,
            JournalEntry.month == month,
        )
    ).all()
    for je in entries:
        for jl in je.lines:
            used_roles.add(jl.role_key)

    for role in used_roles:
        if mapping.get(role) is None:
            issues.append({"severity": "error", "code": "role_unmapped",
                           "message": f"Rol sin cuenta QBO: {role}"})

    # 2. Asientos descuadrados
    for je in entries:
        debe = sum(Decimal(str(jl.amount)) for jl in je.lines if jl.side == "debe")
        haber = sum(Decimal(str(jl.amount)) for jl in je.lines if jl.side == "haber")
        if abs(debe - haber) > Decimal("0.02"):
            issues.append({"severity": "error", "code": "unbalanced",
                           "message": f"Asiento descuadrado: {je.number} ({debe} DR / {haber} CR)"})

    # 3. Gastos sin categoría en el mes
    from datetime import timedelta
    y, m_num = map(int, month.split("-"))
    a = date(y, m_num, 1)
    b = date(y, m_num + 1, 1) - timedelta(days=1) if m_num < 12 else date(y, 12, 31)
    no_cat = db.scalar(
        select(Expense).where(
            Expense.tenant_id == tid,
            Expense.date >= a,
            Expense.date <= b,
            Expense.status != "anulado",
            Expense.category_id.is_(None),
        )
    )
    if no_cat:
        issues.append({"severity": "warning", "code": "expense_no_category",
                       "message": "Hay gastos sin categoría en el mes"})

    # 4. Facturas rechazadas/pendientes en Hacienda con fecha en el mes
    rejected = db.scalar(
        select(Invoice).where(
            Invoice.tenant_id == tid,
            Invoice.issue_date >= a,
            Invoice.issue_date <= b,
            Invoice.einvoice_status.in_(["rechazada", "pendiente", "error"]),
        )
    )
    if rejected:
        issues.append({"severity": "warning", "code": "einvoice_pending",
                       "message": "Hay facturas con estado Hacienda pendiente o rechazado"})

    # 5. Documentos en USD sin tipo de cambio
    no_fx = db.scalar(
        select(Invoice).where(
            Invoice.tenant_id == tid,
            Invoice.issue_date >= a,
            Invoice.issue_date <= b,
            Invoice.currency == "USD",
            Invoice.fx_sell <= 1,
        )
    )
    if no_fx:
        issues.append({"severity": "warning", "code": "missing_fx",
                       "message": "Hay facturas en USD sin tipo de cambio"})

    # 6. Clientes sin cédula
    from ..models import Customer
    bad_customers = db.scalars(
        select(Invoice).where(
            Invoice.tenant_id == tid,
            Invoice.issue_date >= a,
            Invoice.issue_date <= b,
            Invoice.customer_id.isnot(None),
        ).distinct()
    ).all()
    cust_ids = {inv.customer_id for inv in bad_customers if inv.customer_id}
    for cid in cust_ids:
        c = db.get(Customer, cid)
        if c and not c.id_number:
            issues.append({"severity": "warning", "code": "customer_no_tax_id",
                           "message": f"Cliente sin cédula: {c.name}"})
            break  # one warning is enough

    period = _get_or_create_period(db, tid, month)
    db.commit()

    errors = [i for i in issues if i["severity"] == "error"]
    return {
        "month": month,
        "status": period.status,
        "entry_count": len(entries),
        "issues": issues,
        "can_export": len(errors) == 0,
        "generated": result,
    }


# ---------------------------------------------------------------------------
# Exportar mes
# ---------------------------------------------------------------------------

class ExportIn(BaseModel):
    reexport_reason: str | None = None


@router.post("/{month}/export")
def export_month(
    month: str,
    data: ExportIn,
    p: Principal = Depends(require("accounting", "exportar")),
    db: Session = Depends(get_db),
):
    """Exporta el mes como ZIP con:
    (a) asientos_YYYY-MM_N.csv + .xlsx en formato QBO Journal Entries
    (b) contactos_YYYY-MM.csv (clientes y proveedores nuevos del mes)
    (c) resumen_control_YYYY-MM.xlsx

    Cierra el mes (status='exportado') si no está cerrado.
    Si ya está cerrado se requiere reexport_reason y rol admin.
    """
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(422, "Mes inválido (AAAA-MM)")

    tid = p.tenant.id
    period = _get_or_create_period(db, tid, month)

    # Formato de fecha configurado por la empresa
    tenant_obj = db.get(Tenant, tid)
    tenant_settings = (tenant_obj.settings or {}) if tenant_obj else {}
    date_fmt_key = tenant_settings.get("qbo_date_format", _DEFAULT_DATE_FMT)
    date_strfmt = QBO_DATE_FORMATS.get(date_fmt_key, QBO_DATE_FORMATS[_DEFAULT_DATE_FMT])

    is_reexport = period.status == "exportado"
    if is_reexport:
        if not data.reexport_reason:
            raise HTTPException(403, "Para reexportar un mes cerrado indique el motivo y use el permiso admin")
        # Solo admin puede reexportar
        if not p.can("settings", "configurar"):
            raise HTTPException(403, "Solo el administrador puede reexportar un mes cerrado")

    # Validar que no haya errores bloqueantes
    mapping_q = {
        m.role_key: m
        for m in db.scalars(
            select(AccountingRoleMapping).where(AccountingRoleMapping.tenant_id == tid)
        )
    }

    entries = db.scalars(
        select(JournalEntry).where(
            JournalEntry.tenant_id == tid,
            JournalEntry.month == month,
            JournalEntry.export_id.is_(None),
        )
    ).all()

    if not entries and not is_reexport:
        # Intentar regenerar
        js.regenerate_all(db, tid, month)
        entries = db.scalars(
            select(JournalEntry).where(
                JournalEntry.tenant_id == tid,
                JournalEntry.month == month,
                JournalEntry.export_id.is_(None),
            )
        ).all()

    # Resolver nombres de cuentas
    def _account_name(role_key: str) -> str:
        m = mapping_q.get(role_key)
        if m and m.account:
            return m.account.full_name
        return f"[Sin mapear: {role_key}]"

    # Construir filas del CSV de asientos QBO
    qbo_rows: list[dict] = []
    for je in entries:
        for jl in je.lines:
            row: dict[str, str] = {
                "Journal No": je.number,
                "Journal Date": je.date.strftime(date_strfmt),
                "Account": _account_name(jl.role_key),
                "Debits": f"{jl.amount:.2f}" if jl.side == "debe" else "",
                "Credits": f"{jl.amount:.2f}" if jl.side == "haber" else "",
                "Description": jl.description or je.description or "",
                "Name": jl.name or "",
            }
            # Multimoneda
            if jl.currency and jl.currency.upper() != "CRC" and jl.fx:
                row["Currency"] = jl.currency.upper()
                row["Exchange Rate"] = f"{jl.fx:.5f}"
            else:
                row["Currency"] = ""
                row["Exchange Rate"] = ""
            qbo_rows.append(row)

    # Dividir en archivos de QBO_ROWS_PER_FILE filas
    chunks = [qbo_rows[i:i + QBO_ROWS_PER_FILE] for i in range(0, max(len(qbo_rows), 1), QBO_ROWS_PER_FILE)]

    # Crear exportación
    export = AccountingExport(
        tenant_id=tid,
        month=month,
        period_id=period.id,
        exported_by=p.user.id,
        entry_count=len(entries),
        line_count=len(qbo_rows),
        is_reexport=is_reexport,
        reexport_reason=data.reexport_reason,
    )
    db.add(export)
    db.flush()

    # Marcar los asientos como exportados
    for je in entries:
        je.export_id = export.id

    # Cerrar el período
    period.status = "exportado"
    period.exported_at = datetime.now(UTC)
    period.exported_by = p.user.id

    db.commit()

    # Generar ZIP
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Archivos de asientos CSV
        FIELDNAMES = ["Journal No", "Journal Date", "Account", "Debits", "Credits",
                      "Description", "Name", "Currency", "Exchange Rate"]
        for idx, chunk in enumerate(chunks, start=1):
            suffix = f"_{idx}" if len(chunks) > 1 else ""
            csv_buf = io.StringIO()
            w = csv.DictWriter(csv_buf, fieldnames=FIELDNAMES, extrasaction="ignore", lineterminator="\r\n")
            w.writeheader()
            w.writerows(chunk)
            zf.writestr(f"asientos_{month}{suffix}.csv", csv_buf.getvalue().encode("utf-8"))

        # Asientos en XLSX
        try:
            xlsx_buf = _entries_to_xlsx(chunks, month, FIELDNAMES)
            zf.writestr(f"asientos_{month}.xlsx", xlsx_buf.getvalue())
        except Exception:
            pass  # openpyxl opcional; el CSV siempre está

        # Contactos (clientes y proveedores nuevos del mes)
        contacts_csv = _build_contacts_csv(db, tid, month)
        zf.writestr(f"contactos_{month}.csv", contacts_csv.encode("utf-8"))

        # Resumen de control
        try:
            resumen = _build_resumen_xlsx(db, tid, month, entries, mapping_q)
            zf.writestr(f"resumen_control_{month}.xlsx", resumen.getvalue())
        except Exception:
            pass

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=qbo_export_{month}.zip"},
    )


# ---------------------------------------------------------------------------
# Cierre / reapertura de períodos
# ---------------------------------------------------------------------------

class ReopenIn(BaseModel):
    reason: str


@router.post("/{month}/reopen")
def reopen_period(
    month: str,
    data: ReopenIn,
    p: Principal = Depends(require("settings", "configurar")),  # solo admin
    db: Session = Depends(get_db),
):
    """Reabre un mes cerrado (solo admin, con motivo obligatorio)."""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(422, "Mes inválido (AAAA-MM)")
    if not data.reason or len(data.reason.strip()) < 5:
        raise HTTPException(422, "El motivo de reapertura es obligatorio (mínimo 5 caracteres)")

    period = db.scalar(
        select(AccountingPeriod).where(
            AccountingPeriod.tenant_id == p.tenant.id,
            AccountingPeriod.month == month,
        )
    )
    if not period or period.status != "exportado":
        raise HTTPException(404, "Mes no encontrado o no está cerrado")

    period.status = "abierto"
    period.reopened_at = datetime.now(UTC)
    period.reopened_by = p.user.id
    period.reopen_reason = data.reason.strip()
    db.commit()
    return {"month": month, "status": "abierto", "reason": period.reopen_reason}


# ---------------------------------------------------------------------------
# Historial de exportaciones
# ---------------------------------------------------------------------------

@router.get("/exports")
def list_exports(p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(AccountingExport).where(AccountingExport.tenant_id == p.tenant.id)
        .order_by(AccountingExport.created_at.desc())
    ).all()
    return [
        {"id": r.id, "month": r.month, "entry_count": r.entry_count,
         "line_count": r.line_count, "is_reexport": r.is_reexport,
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Helpers de generación de archivos
# ---------------------------------------------------------------------------

def _entries_to_xlsx(chunks: list[list[dict]], month: str, fieldnames: list[str]):
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    wb = openpyxl.Workbook()
    for idx, chunk in enumerate(chunks, start=1):
        ws = wb.create_sheet(f"Asientos {idx}" if len(chunks) > 1 else "Asientos")
        # Header
        ws.append(fieldnames)
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="E2233A")
            cell.font = Font(bold=True, color="FFFFFF")
        for row in chunk:
            ws.append([row.get(f, "") for f in fieldnames])
    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _build_contacts_csv(db: Session, tenant_id: int, month: str) -> str:
    from datetime import timedelta

    from ..models import Customer, Supplier

    y, m = map(int, month.split("-"))
    a = date(y, m, 1)
    b = date(y, m + 1, 1) - timedelta(days=1) if m < 12 else date(y, 12, 31)

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["Name", "Company", "Email", "Phone", "Tax ID"])

    # Clientes que facturaron este mes (filter nuevos del mes sería con Customer.created_at)
    seen: set[int] = set()
    for inv in db.scalars(
        select(Invoice).where(
            Invoice.tenant_id == tenant_id,
            Invoice.issue_date >= a,
            Invoice.issue_date <= b,
            Invoice.customer_id.isnot(None),
        )
    ):
        if inv.customer_id in seen:
            continue
        seen.add(inv.customer_id)
        c = db.get(Customer, inv.customer_id)
        if c:
            w.writerow([c.name, c.name, getattr(c, "email", "") or "", getattr(c, "phone", "") or "", c.id_number or ""])

    # Proveedores del mes
    seen_sup: set[int] = set()
    for exp in db.scalars(
        select(Expense).where(
            Expense.tenant_id == tenant_id,
            Expense.date >= a,
            Expense.date <= b,
            Expense.supplier_id.isnot(None),
        )
    ):
        if exp.supplier_id in seen_sup:
            continue
        seen_sup.add(exp.supplier_id)
        s = db.get(Supplier, exp.supplier_id)
        if s:
            w.writerow([s.name, s.name, s.email or "", s.phone or "", s.tax_id or ""])

    return buf.getvalue()


def _build_resumen_xlsx(
    db: Session, tenant_id: int, month: str,
    entries: list[JournalEntry],
    mapping_q: dict,
) -> io.BytesIO:
    import openpyxl
    from openpyxl.styles import Font

    wb = openpyxl.Workbook()

    # Hoja 1: Totales por cuenta
    ws1 = wb.active
    ws1.title = "Totales por cuenta"
    ws1.append(["Cuenta QBO", "Rol", "Total Debe", "Total Haber", "Neto"])
    for cell in ws1[1]:
        cell.font = Font(bold=True)
    totals: dict[str, list] = {}
    for je in entries:
        for jl in je.lines:
            role = jl.role_key
            m_acc = mapping_q.get(role)
            acc_name = m_acc.account.full_name if (m_acc and m_acc.account) else f"[{role}]"
            if acc_name not in totals:
                totals[acc_name] = [role, Decimal(0), Decimal(0)]
            if jl.side == "debe":
                totals[acc_name][1] += Decimal(str(jl.amount))
            else:
                totals[acc_name][2] += Decimal(str(jl.amount))
    for acc_name, (role, debe, haber) in sorted(totals.items()):
        ws1.append([acc_name, role, float(debe), float(haber), float(debe - haber)])

    # Hoja 2: Documentos incluidos
    ws2 = wb.create_sheet("Documentos")
    ws2.append(["N° Asiento", "Tipo", "Fecha", "Descripción", "Mes contable"])
    for cell in ws2[1]:
        cell.font = Font(bold=True)
    for je in entries:
        ws2.append([je.number, je.source_type, je.date.isoformat(), je.description, je.month])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
