from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, declared_attr, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class TenantMixin:
    """Toda tabla de negocio lleva tenant_id indexado. Regla del plan: multi-tenant desde el dia 1."""

    @declared_attr
    def tenant_id(cls) -> Mapped[int]:  # noqa: N805
        return mapped_column(Integer, ForeignKey("tenant.id", ondelete="RESTRICT"), index=True, nullable=False)
