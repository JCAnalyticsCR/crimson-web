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
    "GAR": ("GAR", "Garantias"),
    # numero provisional de la factura o el tiquete que todavia no se emite en produccion (ver mas abajo)
    "BOR": ("BOR", "Facturas por emitir (numero provisional)"),
}

DOC_TYPE_CODE = {"FE": "01", "ND": "02", "NC": "03", "TE": "04", "FEE": "09", "COT": "00"}

# Los documentos de campo llevan el año a la vista (LEV-2026-0043), como los numera Crimson en sus notas.
# El contador NO se reinicia en enero: el año es parte del nombre, no del consecutivo, y asi nunca se repite
# un numero. Los documentos fiscales conservan su formato: ahi el consecutivo lo manda Hacienda.
WITH_YEAR = ("OPO", "LEV", "PRO", "OT", "SC", "TCK", "CTR", "GAR")


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


# ---------- Numero visible de la factura = consecutivo fiscal (pedido 21/10, seguir la numeracion de Fygaro) ----------
# Ajustes -> Numeracion: "invoice_number_fiscal" (por defecto encendido; solo actua con Produccion activa en Ajustes ->
# Hacienda). Al emitir en PRODUCCION, la factura o el tiquete toma como numero visible la secuencia de su consecutivo de
# 20 digitos (F01 ...0000000087 -> FEC-000087). El consecutivo de Hacienda no cambia: se calcula igual que siempre y
# solo se copia su secuencia. Simulado y pruebas (T0x) no tocan el numero: no es numeracion real.
# Para que dos facturas nunca compartan numero visible, mientras no se emiten las FE/TE llevan un numero PROVISIONAL de
# su propio contador (BOR-000123), que no comparte espacio con FEC/TE: emitir en otro orden, anular un borrador o una
# factura que nunca se emite (aviso de cobro, emitida fuera) no deja un FEC repetido ni un hueco en el FEC visible.
NUMBER_FROM_FISCAL_DEFAULT = True
FISCAL_VISIBLE = ("FE", "TE")
PROVISIONAL = "BOR"


def number_from_fiscal_on(settings: dict | None) -> bool:
    v = (settings or {}).get("invoice_number_fiscal")
    return NUMBER_FROM_FISCAL_DEFAULT if v is None else bool(v)


def provisional_on(db: Session, tenant) -> bool:
    """Las FE/TE nuevas llevan numero provisional: el ajuste encendido y Produccion activa (el interruptor, aunque el
    servidor la bloquee en ese momento: cuando se desbloquee se emiten con su consecutivo)."""
    from . import hacienda_config as hc  # import local: hacienda_config importa modelos y servicios

    return number_from_fiscal_on(tenant.settings) and hc.production_on(db, tenant)


def is_provisional(db: Session, tenant_id: int, number: str | None) -> bool:
    """El numero viene del contador provisional (con el prefijo que tenga hoy; sin grupo, nunca hubo provisionales)."""
    if not number:
        return False
    prefix = db.scalar(select(BillingGroup.prefix).where(BillingGroup.tenant_id == tenant_id, BillingGroup.doc_type == PROVISIONAL, BillingGroup.is_default))
    return prefix is not None and number.startswith(f"{prefix}-")


def provisional_prefix(db: Session, tenant_id: int) -> str | None:
    """Prefijo del contador provisional (None: el tenant nunca tuvo provisionales). Para marcar una lista sin consultar
    por fila."""
    return db.scalar(select(BillingGroup.prefix).where(BillingGroup.tenant_id == tenant_id, BillingGroup.doc_type == PROVISIONAL, BillingGroup.is_default))


def pending_provisional(inv, prefix: str | None) -> bool:
    """La factura todavia lleva el numero provisional y su numero definitivo llega al emitir. Una anulada nunca se emite
    y una "emitida fuera" (Fygaro) ya tiene su comprobante con el numero de alla: ninguna espera numero."""
    return (
        prefix is not None
        and bool(inv.number)
        and inv.number.startswith(f"{prefix}-")
        and inv.status != "anulada"
        and inv.einvoice_status != "emitida_fuera"
    )


_FISCAL_CODE_OF = {"FE": "01", "TE": "04"}


def expected_number(db: Session, tenant, doc_type: str) -> str | None:
    """El numero que tomaria la factura si fuera la proxima en emitirse (solo para mostrarlo: el definitivo lo da la
    emision, y si otra se emite antes, sigue a esa). No crea grupos ni sube contadores."""
    from . import hacienda_config as hc  # import local: hacienda_config importa modelos y servicios

    if doc_type not in FISCAL_VISIBLE:
        return None
    prefix = db.scalar(select(BillingGroup.prefix).where(BillingGroup.tenant_id == tenant.id, BillingGroup.doc_type == doc_type, BillingGroup.is_default))
    prefix = prefix or DEFAULT_GROUPS[doc_type][0]
    if number_from_fiscal_on(tenant.settings) and hc.production_on(db, tenant):
        gtype = fiscal_group_type(_FISCAL_CODE_OF[doc_type], "produccion")
        cur = db.scalar(select(BillingGroup.current).where(BillingGroup.tenant_id == tenant.id, BillingGroup.doc_type == gtype))
    else:
        cur = db.scalar(select(BillingGroup.current).where(BillingGroup.tenant_id == tenant.id, BillingGroup.doc_type == doc_type, BillingGroup.is_default))
    return f"{prefix}-{(cur or 0) + 1:06d}"


def number_taken(db: Session, tenant_id: int, number: str, exclude_id: int | None = None) -> bool:
    """Otra factura del mismo tenant (incluido el historico importado de Fygaro) ya usa ese numero visible."""
    from ..models import Invoice

    stmt = select(Invoice.id).where(Invoice.tenant_id == tenant_id, Invoice.number == number)
    if exclude_id is not None:
        stmt = stmt.where(Invoice.id != exclude_id)
    return db.scalar(stmt.limit(1)) is not None


def visible_from_fiscal(db: Session, tenant_id: int, doc_type: str, consecutive: str) -> str:
    """Numero visible con el prefijo del grupo visible (FEC, TE) y la secuencia del consecutivo fiscal. Sube el contador
    visible del grupo hasta esa secuencia (nunca lo baja): si despues se apaga el ajuste, las facturas nuevas siguen
    despues del ultimo numero que ya salio a Hacienda en vez de repetirlo."""
    if not (consecutive and len(consecutive) == 20 and consecutive.isdigit()):
        raise ValueError("Consecutivo fiscal invalido")
    seq = int(consecutive[-10:])
    g = get_or_create_group(db, tenant_id, doc_type)
    locked = db.execute(select(BillingGroup).where(BillingGroup.id == g.id).with_for_update()).scalar_one()
    if locked.current < seq:
        locked.current = seq
    db.flush()
    return f"{locked.prefix}-{seq:06d}"
