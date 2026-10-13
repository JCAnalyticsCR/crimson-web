"""Inventario de la tienda en linea (reservas por pedido).

STUB: el dueno de este archivo es el frente de inventario de la tienda. Aqui solo estan las firmas que usa el
checkout para que el flujo de pedidos no dependa de esa entrega; no hacen nada.
TODO(integracion): al unir ramas, quedarse con la implementacion real de este archivo (no con este stub).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

# True cuando este modulo reserva y descuenta existencias por pedido. Con True, facturar un pedido que ya
# reservo (held/fulfilled) NO vuelve a descontar inventario (services.store_orders.invoice_order).
# TODO(integracion): la implementacion real lo pone en True.
MANAGES_STOCK = False


def availability(db: Session, tenant_id: int, product_ids: list[int]) -> dict[int, Decimal | None]:
    """Unidades vendibles por producto (None = sin control de existencias). Stub: sin control."""
    return {pid: None for pid in product_ids}


def hold_for_order(db: Session, order) -> None:
    """Reserva las lineas del pedido al crearlo. Stub: no hace nada."""
    return None


def release_for_order(db: Session, order) -> None:
    """Libera la reserva al cancelar o vencer el pedido. Stub: no hace nada."""
    return None


def fulfill_for_order(db: Session, order) -> None:
    """Descuenta definitivamente al enviar o entregar. Stub: no hace nada."""
    return None
