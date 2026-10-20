from sqlalchemy import JSON, Boolean, String, Text
from sqlalchemy import false as sa_false
from sqlalchemy.orm import Mapped, mapped_column

from ..core.db import Base
from .base import ArchiveMixin, TenantMixin, TimestampMixin


class Customer(ArchiveMixin, TenantMixin, TimestampMixin, Base):
    """Cliente reutilizado en cotizaciones, facturas, pagos y XML (receptor v4.4)."""

    __tablename__ = "customer"

    id: Mapped[int] = mapped_column(primary_key=True)
    id_type: Mapped[str] = mapped_column(String(12), default="fisica")  # fisica | juridica | dimex | nite | extranjero
    id_number: Mapped[str | None] = mapped_column(String(30), index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))
    whatsapp: Mapped[str | None] = mapped_column(String(40))
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    address: Mapped[dict | None] = mapped_column(JSON)  # pais, provincia, canton, distrito, senas
    exemption: Mapped[dict | None] = mapped_column(JSON)  # tipo, numero, institucion, fecha, porcentaje
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # cobranza (plan 1.3): este cliente no recibe recordatorios automaticos de cobro (acuerdo de pago, cliente delicado)
    no_reminders: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false())
