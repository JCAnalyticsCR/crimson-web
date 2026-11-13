"""QuickBooks Online: importar plan de cuentas, mapear roles, revisar mes y generar el paquete para la contadora.

Crimson nunca escribe en QuickBooks: entrega archivos que la contadora revisa e importa ella misma.
  - Paquete mensual (services/qb_package.py): clientes, proveedores, facturas de venta y de proveedor en el formato de
    "Importar datos" de QBO, Excel de control (IVA por tarifa, cobros, pagos, D-104) y los XML/PDF de respaldo.
  - Asientos (avanzado): el CSV de asientos de diario de siempre (/qb/{mes}/export).

Permisos:
  GET  /qb/*                         → accounting.ver
  POST /qb/accounts                  → accounting.crear
  PUT  /qb/mapping                   → accounting.editar
  POST /qb/paquete/*/revisado        → accounting.editar (verificación humana del mes)
  POST /qb/paquete/*/generar         → accounting.exportar (el mes tiene que estar revisado)
  POST /qb/*/export                  → accounting.exportar
  POST /qb/*/reopen                  → admin (reabrir mes cerrado)
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
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
    User,
    bank_role,
    expense_cat_role,
)
from ..services import journal as js
from ..services import qb_package as qp
from ..services.documents import audit

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

def _norm_header(h: object) -> str:
    """Cabecera comparable: sin tildes, minúsculas, espacios simples, sin signos y cada palabra en singular
    ("Tipo de detalles" = "tipo de detalle", "Número" = "numero", "N.º de cuenta" = "n de cuenta")."""
    s = unicodedata.normalize("NFKD", str(h or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    words = []
    for w in s.split():
        if w in ("de", "del", "la", "las", "el", "los", "of", "the"):
            continue  # "Nombre de las cuentas" = "Nombre de la cuenta" = "Nombre cuenta"
        if len(w) > 3 and w.endswith("es") and w[:-2].endswith(("on", "or", "al")):
            w = w[:-2]  # descripciones, monedas no: solo los plurales en -es de palabras que terminan en consonante
        elif len(w) > 3 and w.endswith("s"):
            w = w[:-1]
        words.append(w)
    return " ".join(words)


# Cabeceras de la "Lista de cuentas" de QBO (español e inglés, con y sin plural). Las demás columnas
# (Saldo de QuickBooks, Saldo bancario, Impuesto...) se ignoran.
_ACCOUNT_HEADERS: dict[str, str] = {
    _norm_header(k): v
    for k, v in {
        # nombre
        "Nombre de la cuenta": "full_name", "Nombre de cuenta": "full_name", "Nombre": "full_name", "Cuenta": "full_name",
        "Account": "full_name", "Account name": "full_name", "Name": "full_name", "Full name": "full_name",
        # número
        "Número": "number", "Número de cuenta": "number", "N.º de cuenta": "number", "No. de cuenta": "number",
        "Number": "number", "Account number": "number",
        # tipo y tipo de detalle
        "Tipo": "account_type", "Tipo de cuenta": "account_type", "Type": "account_type", "Account type": "account_type",
        "Tipo de detalle": "detail_type", "Tipo de detalles": "detail_type", "Detail type": "detail_type",
        # descripción y moneda
        "Descripción": "description", "Description": "description",
        "Moneda": "currency", "Currency": "currency",
    }.items()
}


def _rows_from_file(content: bytes, filename: str) -> list[list[str]]:
    """Filas crudas de un CSV (coma, punto y coma o tabulador; UTF-8 o Latin-1) o de un XLSX."""
    is_xlsx = (filename or "").lower().endswith((".xlsx", ".xlsm")) or content[:2] == b"PK"
    if is_xlsx:
        try:
            import openpyxl

            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            try:
                ws = wb.active
                return [["" if c is None else str(c) for c in row] for row in ws.iter_rows(values_only=True)]
            finally:
                wb.close()
        except Exception as exc:  # noqa: BLE001 - archivo dañado o que no es Excel: mensaje claro
            raise HTTPException(422, "No se pudo leer el Excel: guardalo de nuevo como .xlsx o exportalo como CSV") from exc
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")  # CSV guardado por Excel en Windows
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        delim = dialect.delimiter
    except csv.Error:
        delim = max((",", ";", "\t"), key=sample.count)
    return [row for row in csv.reader(io.StringIO(text), delimiter=delim)]


def _currency_code(raw: str | None) -> str:
    s = _norm_header(raw)
    if not s:
        return "CRC"
    if re.fullmatch(r"[a-z]{3}", s):
        return s.upper()
    if "usd" in s or "dolar" in s or "dollar" in s:
        return "USD"
    if "eur" in s:
        return "EUR"
    return "CRC"


def _parse_accounts(content: bytes, filename: str) -> list[dict]:
    """Parsea la 'Lista de cuentas' (Plan de cuentas) exportada de QBO, en CSV o XLSX.

    Reconoce las cabeceras en español e inglés sin importar tildes, mayúsculas ni plural (p. ej. el CSV real de
    QBO en español: «Nombre de la cuenta, Tipo de cuenta, Tipo de detalles, Moneda, Saldo de QuickBooks, Saldo
    bancario»). Ignora columnas extra, las filas de título antes de la cabecera, las de totales y las vacías.
    """
    raw_rows = _rows_from_file(content, filename)

    # La cabecera es la primera fila (de las 15 primeras) con la columna del nombre y al menos otra conocida:
    # los informes de QBO traen antes el título, el nombre de la empresa y una línea en blanco.
    header_idx = None
    cols: dict[int, str] = {}
    for i, row in enumerate(raw_rows[:15]):
        found = {j: _ACCOUNT_HEADERS[n] for j, c in enumerate(row) if (n := _norm_header(c)) in _ACCOUNT_HEADERS}
        if "full_name" in found.values() and len(found) >= 2:
            header_idx, cols = i, found
            break
    if header_idx is None:
        return []
    # si dos columnas caen en el mismo campo ("Cuenta" y "Nombre de la cuenta"), gana la primera
    seen_fields: set[str] = set()
    cols = {j: f for j, f in sorted(cols.items()) if not (f in seen_fields or seen_fields.add(f))}
    has_type = "account_type" in cols.values()

    rows = []
    for raw in raw_rows[header_idx + 1 :]:
        row: dict[str, str] = {}
        for j, field_name in cols.items():
            val = raw[j].strip() if j < len(raw) and raw[j] is not None else ""
            if val:
                row[field_name] = val
        if not row.get("full_name"):
            continue
        # filas de totales y pies de informe ("TOTAL", la fecha de generación): sin tipo de cuenta no es una cuenta
        if re.match(r"^total\b", row["full_name"], re.IGNORECASE):
            continue
        if has_type and not row.get("account_type"):
            continue
        row["currency"] = _currency_code(row.get("currency"))
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

    rows = _parse_accounts(content, file.filename or "")  # CSV o XLSX
    if not rows:
        raise HTTPException(422, "No se encontraron cuentas en el archivo. Exportá la «Lista de cuentas» de QuickBooks "
                            "(con la columna «Nombre de la cuenta» o «Cuenta») en CSV o Excel.")

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
         "exported_at": r.exported_at.isoformat() if r.exported_at else None,
         "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None}
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
    users = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_({r.exported_by for r in rows})))} if rows else {}
    return [
        {"id": r.id, "month": r.month, "entry_count": r.entry_count,
         "line_count": r.line_count, "is_reexport": r.is_reexport, "kind": r.kind or "asientos",
         "reexport_reason": r.reexport_reason, "exported_by": users.get(r.exported_by),
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Paquete mensual para la contadora (services/qb_package.py)
# ---------------------------------------------------------------------------
# Flujo con verificación humana: Revisar (Excel de control en borrador) → Marcar revisado → Generar paquete.
# Generar cierra el mes (exportado) y deja el registro (accounting_export kind=paquete: quién, cuándo, qué salió).
# Lo que cambie después sale en el paquete siguiente como ajuste.

def _check_month(month: str) -> None:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(422, "Mes inválido (AAAA-MM)")


def _date_format(db: Session, tenant_id: int) -> tuple[str, str]:
    t = db.get(Tenant, tenant_id)
    key = ((t.settings or {}) if t else {}).get("qbo_date_format", _DEFAULT_DATE_FMT)
    if key not in QBO_DATE_FORMATS:
        key = _DEFAULT_DATE_FMT
    return key, QBO_DATE_FORMATS[key]


def _user_name(db: Session, uid: int | None) -> str | None:
    if not uid:
        return None
    u = db.get(User, uid)
    return u.full_name if u else None


def _period_of(db: Session, tenant_id: int, month: str) -> AccountingPeriod | None:
    return db.scalar(select(AccountingPeriod).where(AccountingPeriod.tenant_id == tenant_id, AccountingPeriod.month == month))


@router.get("/paquete/{month}")
def package_review(month: str, p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    """Qué saldría en el paquete del mes, los avisos y el estado (no escribe nada)."""
    _check_month(month)
    pkg = qp.collect(db, p.tenant.id, month)
    period = _period_of(db, p.tenant.id, month)
    status = period.status if period else "abierto"
    errors = [i for i in pkg.issues if i["severity"] == "error"]
    last = db.scalar(
        select(AccountingExport)
        .where(AccountingExport.tenant_id == p.tenant.id, AccountingExport.month == month, AccountingExport.kind == "paquete")
        .order_by(AccountingExport.created_at.desc(), AccountingExport.id.desc())
        .limit(1)
    )
    return {
        "month": month,
        "status": status,
        "reviewed_at": period.reviewed_at.isoformat() if period and period.reviewed_at else None,
        "reviewed_by": _user_name(db, period.reviewed_by) if period else None,
        "counts": {
            "invoices": len(pkg.invoices), "expenses": len(pkg.expenses), "payments": len(pkg.payments),
            "supplier_payments": len(pkg.supplier_payments), "credit_notes": len(pkg.credit_notes),
            "customers_new": len(pkg.customers_new), "customers_changed": len(pkg.customers_changed),
            "suppliers_new": len(pkg.suppliers_new), "suppliers_changed": len(pkg.suppliers_changed),
            "late": len(pkg.late), "changes": len(pkg.changes),
        },
        "issues": pkg.issues,
        "can_mark_reviewed": status == "abierto" and not errors,
        "can_generate": status in ("revisado", "exportado") and not errors,
        "last_package": {
            "created_at": last.created_at.isoformat() if last.created_at else None,
            "by": _user_name(db, last.exported_by), "documents": last.entry_count, "is_reexport": last.is_reexport,
        } if last else None,
    }


@router.get("/paquete/{month}/control")
def package_control_draft(month: str, p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    """Excel de control en BORRADOR para revisar antes de marcar el mes (no cierra ni registra nada)."""
    _check_month(month)
    pkg = qp.collect(db, p.tenant.id, month)
    key, _fmt = _date_format(db, p.tenant.id)
    data = qp.build_control_xlsx(db, pkg, tenant_name=p.tenant.name, generated_by=p.user.full_name, fmt_date=key, draft=True)
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=control_{month}_borrador.xlsx"},
    )


@router.post("/paquete/{month}/revisado")
def package_mark_reviewed(month: str, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    """Una persona revisó el mes: queda quién y cuándo. Con errores pendientes no se puede marcar."""
    _check_month(month)
    period = _get_or_create_period(db, p.tenant.id, month)
    if period.status == "exportado":
        raise HTTPException(409, "El mes ya tiene paquete (está cerrado). Para cambiarlo, un administrador lo reabre.")
    pkg = qp.collect(db, p.tenant.id, month)
    errors = [i["message"] for i in pkg.issues if i["severity"] == "error"]
    if errors:
        raise HTTPException(409, "Corregí esto antes de marcar el mes como revisado: " + "; ".join(errors[:3]))
    period.status = "revisado"
    period.reviewed_at = datetime.now(UTC)
    period.reviewed_by = p.user.id
    audit(db, p.tenant.id, p.user.id, "revisado", "accounting_period", period.id, {"month": month, "documentos": pkg.count()}, p.ip)
    db.commit()
    return {"month": month, "status": "revisado", "reviewed_at": period.reviewed_at.isoformat(), "reviewed_by": p.user.full_name}


@router.post("/paquete/{month}/quitar-revisado")
def package_unmark_reviewed(month: str, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    """Vuelve el mes a abierto (antes de generar el paquete), p. ej. si falta corregir algo."""
    _check_month(month)
    period = _period_of(db, p.tenant.id, month)
    if period is None or period.status != "revisado":
        raise HTTPException(409, "El mes no está marcado como revisado")
    period.status = "abierto"
    period.reviewed_at = None
    period.reviewed_by = None
    audit(db, p.tenant.id, p.user.id, "quitar_revisado", "accounting_period", period.id, {"month": month}, p.ip)
    db.commit()
    return {"month": month, "status": "abierto"}


class PackageIn(BaseModel):
    reexport_reason: str | None = None


@router.post("/paquete/{month}/generar")
def package_generate(
    month: str,
    data: PackageIn,
    p: Principal = Depends(require("accounting", "exportar")),
    db: Session = Depends(get_db),
):
    """Genera el ZIP del paquete. El mes tiene que estar revisado; al generarlo queda cerrado (exportado) y registrado.
    Volver a generar un mes cerrado: solo el administrador y con motivo (arma el mismo paquete otra vez)."""
    _check_month(month)
    tid = p.tenant.id
    period = _get_or_create_period(db, tid, month)
    is_reexport = period.status == "exportado"
    if period.status == "abierto":
        raise HTTPException(409, "Primero revisá el mes y marcalo como revisado: el paquete sale solo de un mes revisado")
    if is_reexport:
        if not p.can("settings", "configurar"):
            raise HTTPException(403, "Solo el administrador puede volver a generar el paquete de un mes cerrado")
        if not (data.reexport_reason or "").strip():
            raise HTTPException(422, "Indicá el motivo para volver a generar el paquete")
    pkg = qp.collect(db, tid, month)
    errors = [i["message"] for i in pkg.issues if i["severity"] == "error"]
    if errors:
        raise HTTPException(409, "No se puede generar el paquete: " + "; ".join(errors[:3]))

    key, fmt = _date_format(db, tid)
    zip_file, rows = qp.build_zip(db, pkg, tenant_name=p.tenant.name, generated_by=p.user.full_name, fmt_key=key, fmt=fmt)

    export = AccountingExport(
        tenant_id=tid, month=month, period_id=period.id, exported_by=p.user.id, kind="paquete",
        entry_count=pkg.count(), line_count=rows, is_reexport=is_reexport,
        reexport_reason=(data.reexport_reason or "").strip()[:300] or None, manifest=pkg.manifest,
    )
    db.add(export)
    period.status = "exportado"
    period.exported_at = datetime.now(UTC)
    period.exported_by = p.user.id
    db.flush()
    audit(db, tid, p.user.id, "paquete_generado", "accounting_period", period.id,
          {"month": month, "documentos": pkg.count(), "ajustes": len(pkg.changes), "export_id": export.id, "reexport": is_reexport}, p.ip)
    db.commit()

    def _stream():
        try:
            while chunk := zip_file.read(64 * 1024):
                yield chunk
        finally:
            zip_file.close()

    return StreamingResponse(
        _stream(), media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=paquete_quickbooks_{month}.zip"},
    )


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
