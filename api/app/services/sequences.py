"""Consecutivos por grupo de facturacion. Nunca se saltan ni reutilizan: bloqueo de fila por transaccion."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import BillingGroup

DEFAULT_GROUPS = {
    "COT": ("COT", "Cotizaciones"),
    "ORD": ("ORD", "Ordenes de tienda"),
    "FE": ("FEC", "Facturas electronicas"),
    "TE": ("TE", "Tiquetes electronicos"),
    "FEE": ("FEE", "Facturas de exportacion"),
    "NC": ("NC", "Notas de credito"),
    "ND": ("ND", "Notas de debito"),
    "OPO": ("OPO", "Oportunidades"),
    "LEV": ("LEV", "Levantamientos tecnicos"),
    "PRO": ("PRO", "Proyectos"),
    "OT": ("OT", "Ordenes de trabajo"),
    "SC": ("SC", "Solicitudes de compra"),
    "TCK": ("TCK", "Tickets de soporte"),
    "CTR": ("CTR", "Contratos de mantenimiento"),
}

DOC_TYPE_CODE = {"FE": "01", "ND": "02", "NC": "03", "TE": "04", "FEE": "09", "COT": "00"}

# Los documentos de campo llevan el año a la vista (LEV-2026-0043), como los numera Crimson en sus notas.
# El contador NO se reinicia en enero: el año es parte del nombre, no del consecutivo, y asi nunca se repite
# un numero. Los documentos fiscales conservan su formato: ahi el consecutivo lo manda Hacienda.
WITH_YEAR = ("OPO", "LEV", "PRO", "OT", "SC", "TCK", "CTR")


def get_or_create_group(db: Session, tenant_id: int, doc_type: str) -> BillingGroup:
    g = db.scalar(select(BillingGroup).where(BillingGroup.tenant_id == tenant_id, BillingGroup.doc_type == doc_type, BillingGroup.is_default))
    if g is None:
        prefix = DEFAULT_GROUPS.get(doc_type, (doc_type, ""))[0]
        g = BillingGroup(tenant_id=tenant_id, doc_type=doc_type, prefix=prefix, is_default=True)
        db.add(g)
        db.flush()
    return g


def next_number(db: Session, tenant_id: int, doc_type: str) -> tuple[str, str]:
    """Devuelve (numero visible 'FEC-000513', consecutivo v4.4 de 20 digitos)."""
    g = get_or_create_group(db, tenant_id, doc_type)
    # SELECT ... FOR UPDATE en Postgres; en SQLite es un no-op (single writer)
    locked = db.execute(select(BillingGroup).where(BillingGroup.id == g.id).with_for_update()).scalar_one()
    locked.current += 1
    n = locked.current
    db.flush()
    visible = f"{locked.prefix}-{date.today().year}-{n:04d}" if doc_type in WITH_YEAR else f"{locked.prefix}-{n:06d}"
    consecutive = f"{locked.branch}{locked.terminal}{DOC_TYPE_CODE.get(doc_type, '00')}{n:010d}"
    return visible, consecutive


# ---------- Consecutivos fiscales (D5): separados del numero visible ----------
# Se asignan al EMITIR (y de nuevo si Hacienda rechaza). F0x = produccion (debe continuar la numeracion de Fygaro);
# T0x = simulado y pruebas (nunca gastan la numeracion real).
FISCAL_CODES = {
    "01": "Factura electrónica",
    "02": "Nota de débito",
    "03": "Nota de crédito",
    "04": "Tiquete electrónico",
    "05": "Aceptación",
    "06": "Aceptación parcial",
    "07": "Rechazo",
}


def fiscal_group_type(code: str, mode: str) -> str:
    if code not in FISCAL_CODES:
        raise ValueError(f"Código fiscal desconocido: {code}")
    if mode == "produccion":
        return f"F{code}"
    if mode in ("simulado", "pruebas"):
        return f"T{code}"
    raise ValueError(f"Modo fiscal desconocido: {mode}")  # nunca cae por defecto en F0x


def is_fiscal_group(doc_type: str | None) -> bool:
    return bool(doc_type) and len(doc_type) == 3 and doc_type[0] in "FT" and doc_type[1:] in FISCAL_CODES


def ensure_fiscal_groups(db: Session, tenant_id: int) -> dict[str, BillingGroup]:
    """Crea los grupos F01..F07 y T01..T07 que falten (prefijo = el doc_type). Los T0x copian sucursal y terminal de su F0x."""
    have = {g.doc_type: g for g in db.scalars(select(BillingGroup).where(BillingGroup.tenant_id == tenant_id, BillingGroup.doc_type.in_(
        [f"{p}{c}" for p in "FT" for c in FISCAL_CODES]
    )))}
    for code in FISCAL_CODES:
        f = have.get(f"F{code}")
        if f is None:
            f = BillingGroup(tenant_id=tenant_id, doc_type=f"F{code}", prefix=f"F{code}", is_default=True)
            db.add(f)
            db.flush()
            have[f.doc_type] = f
        if f"T{code}" not in have:
            t = BillingGroup(tenant_id=tenant_id, doc_type=f"T{code}", prefix=f"T{code}", branch=f.branch or "001", terminal=f.terminal or "00001", is_default=True)
            db.add(t)
            db.flush()
            have[t.doc_type] = t
    return have


def next_fiscal_consecutive(db: Session, tenant_id: int, code: str, mode: str) -> str:
    """Consecutivo de 20 digitos: sucursal(3) + terminal(5) + tipo(2) + secuencia(10), del grupo F0x/T0x exacto."""
    gtype = fiscal_group_type(code, mode)
    g = db.scalar(select(BillingGroup).where(BillingGroup.tenant_id == tenant_id, BillingGroup.doc_type == gtype))
    if g is None:
        g = ensure_fiscal_groups(db, tenant_id)[gtype]
    locked = db.execute(select(BillingGroup).where(BillingGroup.id == g.id).with_for_update()).scalar_one()
    locked.current += 1
    db.flush()
    return f"{locked.branch}{locked.terminal}{code}{locked.current:010d}"
