"""Inventario de la tienda en linea (reservas por pedido).

STUB con las MISMAS firmas que la implementacion real (rama del frente de inventario). Aqui no hay reservas: se
comporta como "sin control de existencias" y, al facturar, descuenta como siempre (deduct_for_invoice).
TODO(integracion): al unir ramas, quedarse con la implementacion real de este archivo (no con este stub).
"""

from __future__ import annotations

from sqlalchemy.orm import Session


def availability(db: Session, tenant, product_ids, public: bool = True) -> dict[int, dict]:
    """{id: {estado, disponible, mensaje, plazo, cantidad, ultimas, accion, ocultar}}. Stub: todo disponible."""
    return {
        int(pid): {
            "estado": "consultar",
            "disponible": True,
            "mensaje": "",
            "plazo": None,
            "cantidad": None,
            "ultimas": False,
            "accion": None,
            "ocultar": False,
        }
        for pid in product_ids
    }


def hold_for_order(db: Session, o, *, user_id: int | None = None, force: bool = False, commit: bool = True) -> dict:
    """Reserva atomica (llamar con el pedido ya en la sesion, SIN commit previo). Hace commit; si no alcanza: 409."""
    if commit:
        db.commit()
    return {"reservas": [], "bajo_pedido": [], "requiere_compra": False, "solicitud_compra": None, "vence": None}


def mark_paid(db: Session, o, user_id: int | None = None) -> dict:
    return {"reservas_fijas": 0}


def release_for_order(db: Session, o, note: str | None = None, user_id: int | None = None) -> int:
    return 0


def fulfill_for_order(db: Session, o, user_id: int | None = None, invoice=None) -> int:
    return 0


def deduct_for_order_invoice(db: Session, o, invoice, user_id: int | None = None) -> int:
    """Reemplaza inventory.deduct_for_invoice al facturar un pedido (la real no descuenta dos veces)."""
    from . import inventory as invsvc

    return invsvc.deduct_for_invoice(db, invoice, user_id)


def on_order_status(db: Session, o, old: str | None, new: str, user_id: int | None = None) -> dict:
    """pagado/preparando fija la reserva, enviado/entregado descuenta, cancelado libera."""
    if new == old:
        return {}
    if new in ("pagado", "preparando") and old == "nuevo":
        return mark_paid(db, o, user_id)
    if new in ("enviado", "entregado"):
        return {"movimientos": fulfill_for_order(db, o, user_id)}
    if new == "cancelado":
        return {"liberadas": release_for_order(db, o, None, user_id)}
    return {}
