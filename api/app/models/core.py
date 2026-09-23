from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base
from .base import TenantMixin, TimestampMixin


class Tenant(TimestampMixin, Base):
    __tablename__ = "tenant"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    legal_name: Mapped[str | None] = mapped_column(String(200))
    tax_id: Mapped[str | None] = mapped_column(String(30))  # cedula juridica
    sector: Mapped[str | None] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(String(5), default="es")
    default_currency: Mapped[str] = mapped_column(String(3), default="CRC")
    logo_url: Mapped[str | None] = mapped_column(String(400))
    plan: Mapped[str] = mapped_column(String(30), default="trial")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)  # facturacion, notificaciones, legal, etc.

    users: Mapped[list["TenantUser"]] = relationship(back_populates="tenant")


class User(TimestampMixin, Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160))
    password_hash: Mapped[str] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_recovery: Mapped[list] = mapped_column(JSON, default=list)  # hashes de los codigos de un solo uso
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list["TenantUser"]] = relationship(back_populates="user")


class Role(Base):
    """Roles predefinidos: admin, ventas, caja, inventario, contabilidad, lectura. permissions = {modulo: [acciones]}."""

    __tablename__ = "role"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    permissions: Mapped[dict] = mapped_column(JSON, default=dict)


class TenantUser(TimestampMixin, Base):
    __tablename__ = "tenant_user"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_tenant_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    role_code: Mapped[str] = mapped_column(ForeignKey("role.code"), default="lectura")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    tenant: Mapped[Tenant] = relationship(back_populates="users")
    user: Mapped[User] = relationship(back_populates="memberships")
    role: Mapped[Role] = relationship()


class Invitation(TenantMixin, TimestampMixin, Base):
    __tablename__ = "invitation"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), index=True)
    role_code: Mapped[str] = mapped_column(String(40))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invited_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))


class RefreshToken(Base):
    """Refresh tokens rotativos: se guarda solo el hash; al usarse se revoca y se emite otro."""

    __tablename__ = "refresh_token"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    ip: Mapped[str | None] = mapped_column(String(64))


class AuditLog(Base):
    """Toda creacion/edicion/anulacion de documentos y pagos: usuario, fecha, IP y diff."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ip: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(40))  # create | update | void | pay | send | login ...
    entity: Mapped[str] = mapped_column(String(40))  # quote | invoice | payment | customer ...
    entity_id: Mapped[int | None] = mapped_column(Integer)
    diff: Mapped[dict | None] = mapped_column(JSON)


class Currency(TenantMixin, Base):
    __tablename__ = "currency"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_currency_tenant"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(3))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class ExchangeRate(Base):
    """Tipo de cambio BCCR (venta/compra) por fecha. Fuente: bccr | manual."""

    __tablename__ = "exchange_rate"
    __table_args__ = (UniqueConstraint("date", "currency", name="uq_fx_date_currency"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    sell: Mapped[float] = mapped_column(Numeric(12, 5))
    buy: Mapped[float] = mapped_column(Numeric(12, 5))
    source: Mapped[str] = mapped_column(String(20), default="bccr")
    note: Mapped[str | None] = mapped_column(Text)
