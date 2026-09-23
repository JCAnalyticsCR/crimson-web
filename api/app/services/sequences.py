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
}

DOC_TYPE_CODE = {"FE": "01", "ND": "02", "NC": "03", "TE": "04", "FEE": "09", "COT": "00"}

# Los documentos de campo llevan el año a la vista (LEV-2026-0043), como los numera Crimson en sus notas.
# El contador NO se reinicia en enero: el año es parte del nombre, no del consecutivo, y asi nunca se repite
# un numero. Los documentos fiscales conservan su formato: ahi el consecutivo lo manda Hacienda.
WITH_YEAR = ("OPO", "LEV", "PRO", "OT", "SC")


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
