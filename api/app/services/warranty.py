"""Garantias (Andres, 19/10): el equipo del cliente pasa primero por una revision de Crimson y, si procede, se manda al
distribuidor, que tiene taller especializado. Vuelve reparado, reemplazado o rechazado y se le entrega al cliente.

Misma regla que facturas, proyectos y pedidos web: el caso dice donde deberia estar cada unidad y `reconcile` escribe
en el kardex solo la diferencia (idempotente, con el caso y el usuario en cada movimiento):

- recibido / devuelto  -> la unidad esta en la bodega de revision de Crimson;
- en_distribuidor      -> en la bodega del distribuidor;
- cerrado / anulado    -> ya no esta en manos de Crimson.
- Reemplazo de Crimson: si se le dio al cliente un equipo de la bodega de venta mientras tanto, ese equipo sale de la
  venta (salvo anulado) y la unidad que vuelve reparada o cambiada del distribuidor entra a la venta: el proveedor no
  acepta devoluciones, asi que queda como mercaderia de Crimson.

Las bodegas de revision y del distribuidor nunca se venden (Warehouse.kind); se crean solas la primera vez.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import CustomerAsset, Product, StockMovement, WarrantyCase
from . import inventory as invsvc

ZERO = Decimal(0)
STATE_LABEL = {
    "recibido": "En revisión de Crimson",
    "en_distribuidor": "En el distribuidor",
    "devuelto": "De vuelta en Crimson",
    "cerrado": "Cerrada",
    "anulado": "Anulada",
}
RESULT_LABEL = {"reparado": "Reparado", "reemplazado": "Reemplazado por el distribuidor", "rechazado": "Garantía rechazada"}
REVIEW_LABEL = {"procede": "Procede", "no_procede": "No procede"}
OPEN = ("recibido", "en_distribuidor", "devuelto")


def _d(v) -> Decimal:
    return Decimal(str(v or 0))


def _goods(db: Session, pid: int | None, tenant_id: int) -> Product | None:
    p = db.get(Product, pid) if pid else None
    return p if p is not None and p.tenant_id == tenant_id and p.item_type == "producto" else None


def targets(db: Session, c: WarrantyCase) -> dict[tuple[int, int], Decimal]:
    """Donde deberia estar cada cosa de este caso HOY, por (producto, bodega)."""
    out: dict[tuple[int, int], Decimal] = {}

    def add(pid: int, wid: int, q: Decimal) -> None:
        out[(pid, wid)] = out.get((pid, wid), ZERO) + q

    q = _d(c.quantity) or Decimal(1)
    gave_replacement = _d(c.replacement_qty) > 0 and _goods(db, c.replacement_product_id, c.tenant_id) is not None
    if _goods(db, c.product_id, c.tenant_id) is not None:
        if c.status == "en_distribuidor":
            add(c.product_id, invsvc.special_warehouse(db, c.tenant_id, "proveedor").id, q)
        elif c.status == "recibido" or (c.status == "devuelto" and not (gave_replacement and c.result in ("reparado", "reemplazado"))):
            add(c.product_id, invsvc.special_warehouse(db, c.tenant_id, "revision").id, q)
        elif c.status in ("devuelto", "cerrado") and gave_replacement and c.result in ("reparado", "reemplazado"):
            # el cliente ya tiene el reemplazo de Crimson: lo que vuelve del distribuidor es mercaderia de Crimson
            w = invsvc.default_warehouse(db, c.tenant_id)
            if w is not None:
                add(c.product_id, w.id, q)
    if gave_replacement and c.status != "anulado":
        w = invsvc.default_warehouse(db, c.tenant_id)
        if w is not None:
            add(c.replacement_product_id, w.id, -_d(c.replacement_qty))
    return out


def moved(db: Session, c: WarrantyCase) -> dict[tuple[int, int], Decimal]:
    rows = db.execute(
        select(StockMovement.product_id, StockMovement.warehouse_id, func.coalesce(func.sum(StockMovement.quantity), 0))
        .where(StockMovement.tenant_id == c.tenant_id, StockMovement.warranty_id == c.id)
        .group_by(StockMovement.product_id, StockMovement.warehouse_id)
    ).all()
    return {(pid, wid): _d(q) for pid, wid, q in rows}


def reconcile(db: Session, c: WarrantyCase, user_id: int | None, reason: str | None = None) -> int:
    """Escribe en el kardex solo la diferencia entre donde deberia estar cada unidad y lo ya registrado (sin commit)."""
    want, have = targets(db, c), moved(db, c)
    # la unidad que entra de vuelta puede ser otra: si el distribuidor la cambio, su serie es la nueva
    back = c.status in ("devuelto", "cerrado") and c.new_serial
    n = 0
    for key in sorted(set(want) | set(have)):
        delta = want.get(key, ZERO) - have.get(key, ZERO)
        if delta == 0:
            continue
        pid, wid = key
        if pid == c.replacement_product_id and delta < 0 and _d(c.replacement_qty) > 0:
            serial = c.replacement_serial
        elif back and delta > 0:
            serial = c.new_serial
        else:
            serial = c.serial
        invsvc.move(
            db,
            c.tenant_id,
            user_id,
            pid,
            wid,
            "garantia",
            delta,
            c.number,
            reason,
            None,
            movement_type="garantia",
            warranty_id=c.id,
            serial=serial or None,
        )
        n += 1
    return n


def note(c: WarrantyCase, user_id: int | None, text: str, status: str | None = None) -> None:
    c.history = [
        *(c.history or []),
        {"at": datetime.now(UTC).isoformat(), "by": user_id, "status": status or c.status, "note": text[:500]},
    ]


def _asset(db: Session, c: WarrantyCase) -> CustomerAsset | None:
    a = db.get(CustomerAsset, c.asset_id) if c.asset_id else None
    return a if a is not None and a.tenant_id == c.tenant_id else None


def apply(db: Session, c: WarrantyCase, action: str, data: dict, user_id: int | None) -> int:
    """Cada paso del caso: valida el orden, anota el historial, actualiza el equipo del cliente y concilia el kardex."""
    now = datetime.now(UTC)
    st = c.status
    if st in ("cerrado", "anulado"):
        raise HTTPException(409, f"La garantía {c.number} ya está {STATE_LABEL[st].lower()}")

    if action == "revisar":
        if st != "recibido":
            raise HTTPException(409, "La revisión de Crimson se hace con el equipo recibido, antes de mandarlo al distribuidor")
        c.review, c.review_note, c.reviewed_by, c.reviewed_at = data["review"], data.get("note"), user_id, now
        note(c, user_id, f"Revisión de Crimson: {REVIEW_LABEL[c.review].lower()}" + (f" · {data['note']}" if data.get("note") else ""))
    elif action == "enviar":
        if st != "recibido":
            raise HTTPException(409, "Solo se manda al distribuidor un equipo que está en revisión de Crimson")
        if c.review != "procede":
            raise HTTPException(409, "Primero la revisión de Crimson tiene que decir que la garantía procede")
        c.status, c.sent_at = "en_distribuidor", now
        c.supplier_id = data.get("supplier_id") or c.supplier_id
        c.supplier_ref = data.get("supplier_ref") or c.supplier_ref
        note(
            c,
            user_id,
            "Enviado al distribuidor" + (f" · boleta {c.supplier_ref}" if c.supplier_ref else "") + (f" · {data['note']}" if data.get("note") else ""),
        )
    elif action == "recibir":
        if st != "en_distribuidor":
            raise HTTPException(409, "Solo vuelve del distribuidor un equipo que se le mandó")
        c.status, c.returned_at, c.result = "devuelto", now, data["result"]
        c.new_serial = (data.get("new_serial") or "").strip() or None
        note(
            c,
            user_id,
            f"Volvió del distribuidor: {RESULT_LABEL[c.result].lower()}"
            + (f" · serie nueva {c.new_serial}" if c.new_serial else "")
            + (f" · {data['note']}" if data.get("note") else ""),
        )
    elif action == "reemplazar":
        if st not in OPEN:
            raise HTTPException(409, "El reemplazo se da mientras la garantía está abierta")
        p = _goods(db, data.get("product_id"), c.tenant_id)
        if p is None:
            raise HTTPException(422, "El reemplazo tiene que ser un producto del catálogo")
        if _d(c.replacement_qty) > 0:
            raise HTTPException(409, "Esta garantía ya tiene un reemplazo registrado")
        c.replacement_product_id, c.replacement_qty = p.id, _d(data.get("quantity") or 1)
        c.replacement_serial = (data.get("serial") or "").strip() or None
        note(c, user_id, f"Crimson le dio un reemplazo de su bodega: {p.name}" + (f" · serie {c.replacement_serial}" if c.replacement_serial else ""))
    elif action == "cerrar":
        if st == "en_distribuidor":
            raise HTTPException(409, "El equipo sigue en el distribuidor: registrá primero que volvió")
        if st == "recibido" and c.review != "no_procede":
            raise HTTPException(409, "Para cerrar sin pasar por el distribuidor, la revisión de Crimson tiene que decir que no procede")
        c.status, c.closed_at, c.closing_note = "cerrado", now, data.get("note")
        note(c, user_id, "Cerrada: equipo entregado al cliente" + (f" · {data['note']}" if data.get("note") else ""))
    elif action == "anular":
        c.status, c.closed_at, c.closing_note = "anulado", now, data.get("note")
        note(c, user_id, "Anulada" + (f" · {data['note']}" if data.get("note") else ""))
    else:
        raise HTTPException(422, "Acción desconocida")

    a = _asset(db, c)
    if a is not None:
        if c.status in OPEN:
            a.status = "garantia"
        else:
            a.status = "activo"
            serie = c.replacement_serial if (_d(c.replacement_qty) > 0 and c.status == "cerrado") else c.new_serial
            if c.status == "cerrado" and serie and serie != a.serial:
                a.notes = ((a.notes or "") + f"\n{c.number}: serie {a.serial or '—'} → {serie}").strip()
                a.serial = serie
    return reconcile(db, c, user_id, f"Garantía · {STATE_LABEL[c.status].lower()}")
