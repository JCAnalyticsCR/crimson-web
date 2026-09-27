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


class ArchiveMixin:
    """Archivo y papelera (lo pidio Andres: "documentos archivados y proximos a borrar, para si hay errores").

    archived_at: sale de las listas activas pero se conserva para siempre y se restaura.
    trashed_at: "proximo a borrar"; el worker lo purga despues de N dias (tenant.settings.archive_purge_days).
    Los *_by son enteros sin FK a proposito: la auditoria de la purga guarda quien fue aunque el usuario ya no exista.
    """

    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    archived_by: Mapped[int | None] = mapped_column(Integer)
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    trashed_by: Mapped[int | None] = mapped_column(Integer)
