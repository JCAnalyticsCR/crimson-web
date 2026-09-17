"""Consecutivos por grupo de facturacion. Nunca se saltan ni reutilizan: bloqueo de fila por transaccion."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import BillingGroup

DEFAULT_GROUPS = {
    "COT": ("COT", "Cotizaciones"),
    "FE": ("FEC", "Facturas electronicas"),
    "TE": ("TE", "Tiquetes electronicos"),
    "FEE": ("FEE", "Facturas de exportacion"),
    "NC": ("NC", "Notas de credito"),
    "ND": ("ND", "Notas de debito"),
}

DOC_TYPE_CODE = {"FE": "01", "ND": "02", "NC": "03", "TE": "04", "FEE": "09", "COT": "00"}


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
    visible = f"{locked.prefix}-{n:06d}"
    consecutive = f"{locked.branch}{locked.terminal}{DOC_TYPE_CODE.get(doc_type, '00')}{n:010d}"
    return visible, consecutive
