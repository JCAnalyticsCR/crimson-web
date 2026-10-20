"""Una sola definicion de los estados de factura para el cobro (plan 1.3).

Antes cada pantalla tenia la suya: "por cobrar" no incluia las enviadas, el worker volvia "vencida" un borrador que el
cliente nunca recibio (y el aliado lo veia como deuda) y los recordatorios salian sobre borradores. Ahora:

- ENVIADA: le llego al cliente. Estados que solo existen despues de enviar (enviada, parcial, vencida, pagada) o
  `sent_at` (correo aceptado por el proveedor, enlace de pago creado para compartir, comprobante fiscal entregado o envio
  automatico de la recurrencia). Un "creado" sin sent_at es un borrador.
- VISIBLE: lo que ven el portal del cliente y el del aliado: enviada (o aceptada por Hacienda) y no anulada.
- COBRABLE: enviada, no anulada ni pagada y con saldo. Es "por cobrar" en el inicio, la lista y los reportes.
- VENCIBLE: cobrable que todavia no esta "vencida": lo unico que el worker puede pasar a vencida.

Cada definicion existe como filtro SQL (*_where) y como predicado de Python (is_*), y los dos dicen lo mismo.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import and_, or_

from ..models import Invoice

SENT_STATES = ("enviada", "parcial", "vencida", "pagada")  # solo existen despues de enviar (o de cobrar)
VISIBLE = SENT_STATES
COBRABLE = ("creado", "enviada", "parcial", "vencida")  # "creado" solo con sent_at
VENCIBLE = ("creado", "enviada", "parcial")  # "creado" solo con sent_at


def cr_today() -> date:
    """Fecha de Costa Rica: el contenedor corre en UTC y despues de las 18:00 CR la fecha UTC ya es la de manana
    (una factura que vence hoy saldria "vencida" desde las 6 p. m.)."""
    from ..providers.einvoice import cr_now

    return cr_now().date()


# ---------- predicados ----------
def is_sent(inv: Invoice) -> bool:
    return inv.sent_at is not None or inv.status in SENT_STATES


def is_visible(inv: Invoice) -> bool:
    return inv.status != "anulada" and (is_sent(inv) or inv.einvoice_status == "aceptada")


def is_cobrable(inv: Invoice) -> bool:
    return inv.status in COBRABLE and is_sent(inv) and Decimal(str(inv.balance or 0)) > 0


def is_vencible(inv: Invoice, today: date) -> bool:
    return inv.status in VENCIBLE and is_cobrable(inv) and inv.due_date is not None and inv.due_date < today


# ---------- filtros SQL ----------
def sent_where():
    return or_(Invoice.sent_at.is_not(None), Invoice.status.in_(SENT_STATES))


def visible_where():
    return and_(Invoice.status != "anulada", or_(sent_where(), Invoice.einvoice_status == "aceptada"))


def cobrable_where():
    return and_(Invoice.status.in_(COBRABLE), sent_where(), Invoice.balance > 0)


def vencible_where(today: date):
    return and_(Invoice.status.in_(VENCIBLE), sent_where(), Invoice.balance > 0, Invoice.due_date.is_not(None), Invoice.due_date < today)


def overdue_where(today: date):
    """Cobrable con la fecha de vencimiento pasada (este o no marcada "vencida" todavia)."""
    return and_(cobrable_where(), Invoice.due_date.is_not(None), Invoice.due_date < today)


# ---------- transicion ----------
def mark_sent(inv: Invoice, when: datetime | None = None) -> None:
    """La factura le llego al cliente: queda la primera fecha (el reenvio no la mueve). No cambia el estado: pasar de
    "creado" a "enviada" es del envio por correo (services/documents_send.py)."""
    if inv.sent_at is None:
        inv.sent_at = when or datetime.now(UTC)
