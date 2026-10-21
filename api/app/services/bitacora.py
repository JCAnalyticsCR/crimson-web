"""Bitacora con antes/despues.

snapshot() fotografia las columnas de un modelo en valores JSON (Decimal y fechas como texto) y changes() deja solo
lo que cambio. audit_change() junta las dos cosas y escribe la fila con before/after, para que el visor de Ajustes
muestre "de X a Y" campo por campo. Las mas de cien llamadas viejas a audit() siguen igual (solo diff).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from ..models import AuditLog
from .documents import audit

# Nunca a la bitacora: binarios, secretos y marcas que cambian solas
_SKIP = {
    "data",
    "password_hash",
    "secret_encrypted",
    "totp_secret",
    "totp_pending_secret",
    "updated_at",
    "created_at",
    # Hacienda y recepcion: credenciales cifradas y XML completos (el XML no es un "cambio" legible y pesa)
    "atv_user_enc",
    "atv_password_enc",
    "p12_enc",
    "p12_pin_enc",
    "raw_eml",
    "xml_document",
    "xml_response",
    "xml_supplier_response",
    "xml_signed",
    "hacienda_xml",
    "receiver_xml",
}


def jsonable(v):
    if isinstance(v, Decimal):
        # 1500.00000 -> "1500": se lee igual en el visor y no aparece un cambio falso por la escala
        n = v.normalize()
        return f"{n:f}" if n != n.to_integral_value() else str(int(n))
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, bytes):
        return None
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [jsonable(x) for x in v]
    return v


def snapshot(obj, fields=None, exclude=()) -> dict:
    """Columnas del modelo (o solo `fields`) como dict JSON. No toca relaciones ni columnas diferidas."""
    mapper = inspect(obj).mapper
    names = fields or [c.key for c in mapper.column_attrs]
    out = {}
    for k in names:
        if k in _SKIP or k in exclude or k in ("id", "tenant_id"):
            continue
        out[k] = jsonable(getattr(obj, k, None))
    return out


def changes(before: dict, after: dict) -> tuple[dict, dict]:
    """(antes, despues) solo con las llaves que cambiaron."""
    b, a = {}, {}
    for k in dict.fromkeys([*before, *after]):
        if before.get(k) != after.get(k):
            b[k], a[k] = before.get(k), after.get(k)
    return b, a


def rows_by(rows, key: str, fields: tuple[str, ...]) -> dict[str, dict]:
    """Hijos (puntos, materiales) indexados por una llave estable (el codigo del punto: su id cambia al guardar)."""
    out: dict[str, dict] = {}
    for r in rows or []:
        k = str(getattr(r, key, None) or "")
        if k in out:  # llave repetida: se distingue para no perder el renglon
            k = f"{k}#{len(out)}"
        out[k] = {f: jsonable(getattr(r, f, None)) for f in fields}
    return out


def children_diff(before: dict[str, dict], after: dict[str, dict]) -> tuple[dict, dict, dict]:
    """(resumen, antes, despues). resumen: llaves agregadas/quitadas/cambiadas. antes/despues: lo quitado o
    agregado completo y, de lo cambiado, solo los campos que cambiaron."""
    added = [k for k in after if k not in before]
    removed = [k for k in before if k not in after]
    changed = [k for k in after if k in before and after[k] != before[k]]
    b: dict = {k: before[k] for k in removed}
    a: dict = {k: after[k] for k in added}
    for k in changed:
        cb, ca = changes(before[k], after[k])
        b[k], a[k] = cb, ca
    resumen = {}
    if added:
        resumen["agregados"] = added
    if removed:
        resumen["quitados"] = removed
    if changed:
        resumen["cambiados"] = changed
    return resumen, b, a


POINT_FIELDS = ("kind", "label", "data", "photos", "notes")
ITEM_FIELDS = ("product_id", "quantity", "unit", "note", "kind")  # sin unit_cost: el costeo audita aparte


def survey_state(s) -> dict:
    """Foto del levantamiento antes de guardar: campos, puntos por codigo y materiales por nombre."""
    return {
        "campos": snapshot(s),
        "puntos": rows_by(s.points, "code", POINT_FIELDS),
        "materiales": rows_by(s.items, "name", ITEM_FIELDS),
    }


def audit_survey_update(db: Session, p, s, antes: dict, company: str | None = None) -> bool:
    """Lo que cambio al guardar el levantamiento: campos con antes/despues y resumen de puntos (por codigo) y
    materiales (por nombre) agregados, quitados y cambiados. No toca el guardado: solo compara dos fotos."""
    despues = survey_state(s)
    fb, fa = changes(antes["campos"], despues["campos"])
    res_p, pb, pa = children_diff(antes["puntos"], despues["puntos"])
    res_m, mb, ma = children_diff(antes["materiales"], despues["materiales"])
    if not (fb or fa or res_p or res_m):
        return False
    before, after = dict(fb), dict(fa)
    if res_p:
        before["puntos"], after["puntos"] = pb, pa
    if res_m:
        before["materiales"], after["materiales"] = mb, ma
    diff: dict = {"campos": list(fa)}
    if res_p:
        diff["puntos"] = res_p
    if res_m:
        diff["materiales"] = res_m
    audit(
        db,
        p.tenant.id,
        p.user.id,
        "update",
        "survey",
        s.id,
        diff,
        ip=p.ip,
        before=before,
        after=after,
        version=next_version(db, p.tenant.id, "survey", s.id),
        company=company,
    )
    return True


def next_version(db: Session, tenant_id: int, entity: str, entity_id: int | None, action: str = "update") -> int:
    """Numero de edicion del registro: cuantas veces se audito esta accion sobre el, mas uno."""
    n = db.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.tenant_id == tenant_id, AuditLog.entity == entity, AuditLog.entity_id == entity_id, AuditLog.action == action
        )
    )
    return int(n or 0) + 1


def audit_change(
    db: Session,
    p,
    action: str,
    entity: str,
    entity_id: int | None,
    before: dict,
    after: dict,
    *,
    reason: str | None = None,
    version=None,
    company: str | None = None,
    extra: dict | None = None,
    always: bool = False,
) -> bool:
    """Escribe la fila solo si algo cambio (o `always`). diff lleva la lista de campos para quien lea lo viejo."""
    b, a = changes(before, after)
    if not b and not a and not extra and not always:
        return False
    diff = {"campos": list(a)}
    if extra:
        diff.update(extra)
    audit(
        db,
        p.tenant.id,
        p.user.id,
        action,
        entity,
        entity_id,
        diff,
        ip=p.ip,
        before=b or None,
        after=a or None,
        reason=reason,
        version=version,
        company=company,
    )
    return True
