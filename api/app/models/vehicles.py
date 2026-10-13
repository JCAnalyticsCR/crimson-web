"""Vehiculos y combustible (documento de Andres, seccion 12 / AC21).

Nada se borra para "limpiar" el analisis: un vehiculo o una carga se marca excluida (con motivo) y deja de
contar en los reportes, pero sigue en la lista con su marca. Asi la historia queda completa y auditable.
"""

from __future__ import annotations

from datetime import date as Date

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Text, false, true
from sqlalchemy import Date as SaDate
from sqlalchemy.orm import Mapped, mapped_column

from ..core.db import Base
from .base import TenantMixin, TimestampMixin


class Vehicle(TenantMixin, TimestampMixin, Base):
    __tablename__ = "vehicle"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))  # alias con el que lo conoce la gente
    plate: Mapped[str | None] = mapped_column(String(20), index=True)
    kind: Mapped[str | None] = mapped_column(String(30))  # sedan | pickup | suv | moto | camion | otro
    owner: Mapped[str] = mapped_column(String(10), default="empresa", server_default="empresa")  # empresa | personal
    driver: Mapped[str | None] = mapped_column(String(120))  # conductor habitual
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    # AC21: fuera del analisis sin borrar la historia (p. ej. un vehiculo personal que cargaba con la tarjeta)
    excluded_from_analysis: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    exclusion_reason: Mapped[str | None] = mapped_column(String(240))
    notes: Mapped[str | None] = mapped_column(Text)


class FuelLoad(TenantMixin, TimestampMixin, Base):
    """Una compra de combustible. Comprar no es consumir: el reporte mensual habla de compras."""

    __tablename__ = "fuel_load"

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.id", ondelete="RESTRICT"), index=True)
    date: Mapped[Date] = mapped_column(SaDate, index=True)
    driver: Mapped[str | None] = mapped_column(String(120))
    liters: Mapped[float | None] = mapped_column(Numeric(12, 3))
    amount: Mapped[float] = mapped_column(Numeric(16, 2))
    currency: Mapped[str] = mapped_column(String(3), default="CRC", server_default="CRC")
    odometer: Mapped[int | None] = mapped_column(Integer)
    full_tank: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())  # lleno | parcial
    receipt_media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id", ondelete="SET NULL"))
    usage: Mapped[str | None] = mapped_column(String(160))  # para que fue (visita, entrega, proyecto...)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id", ondelete="SET NULL"), index=True)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    exclusion_reason: Mapped[str | None] = mapped_column(String(240))
    source: Mapped[str] = mapped_column(String(12), default="manual", server_default="manual")  # manual | importacion
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))


class VehicleCost(TenantMixin, TimestampMixin, Base):
    """Costos del vehiculo aparte del combustible (opcional): mantenimiento, llantas, seguro, depreciacion."""

    __tablename__ = "vehicle_cost"

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.id", ondelete="RESTRICT"), index=True)
    date: Mapped[Date] = mapped_column(SaDate, index=True)
    kind: Mapped[str] = mapped_column(String(20))  # mantenimiento | llantas | seguro | depreciacion | otro
    amount: Mapped[float] = mapped_column(Numeric(16, 2))
    currency: Mapped[str] = mapped_column(String(3), default="CRC", server_default="CRC")
    description: Mapped[str | None] = mapped_column(String(240))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
