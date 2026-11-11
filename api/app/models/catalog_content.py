"""Lotes de contenido de la tienda (O3-11): ver services/catalog_drafts."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..core.db import Base
from .base import TenantMixin


class CatalogBatch(TenantMixin, Base):
    """Lote de contenido de la tienda (O3-11): un grupo de borradores de un mismo campo que se revisa, se aprueba, se
    aplica y se puede deshacer junto. Nada de lo propuesto se ve en la tienda hasta que un administrador aplica el lote.

    status: propuesto | aplicado | deshecho | descartado. sample: la muestra revisada a mano antes de aplicar
    {checked: [ids], wrong: [ids], at, by}; sin muestra suficiente (50 o todo el lote) y con 2 % o mas de error no se
    aplica. source: manual (pedido desde el panel o el guion) | alta (productos nuevos de la sincronizacion)."""

    __tablename__ = "catalog_batch"

    id: Mapped[int] = mapped_column(primary_key=True)
    field: Mapped[str] = mapped_column(String(30))
    rule: Mapped[str] = mapped_column(String(40))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("category.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(10), default="manual")
    status: Mapped[str] = mapped_column(String(12), default="propuesto", index=True)
    note: Mapped[str | None] = mapped_column(String(300))
    sample: Mapped[dict | None] = mapped_column(JSON)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_by: Mapped[int | None] = mapped_column(Integer)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    undone_by: Mapped[int | None] = mapped_column(Integer)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CatalogDraft(TenantMixin, Base):
    """Borrador de un campo web_ de un producto (O3-11). value: lo propuesto; previous: lo que habia al aplicar (para
    deshacer exacto). status: propuesto | aprobado | aplicado | descartado | deshecho. flag: por que va a revision
    («sin tipo», «largo», «codigo al final»...). Un solo borrador abierto (propuesto o aprobado) por producto y campo."""

    __tablename__ = "catalog_draft"
    __table_args__ = (Index("ix_catalog_draft_product_field", "product_id", "field"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("catalog_batch.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"))
    field: Mapped[str] = mapped_column(String(30))
    value: Mapped[dict | list | str | None] = mapped_column(JSON)
    previous: Mapped[dict | list | str | None] = mapped_column(JSON)
    rule: Mapped[str] = mapped_column(String(40))
    flag: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(12), default="propuesto", index=True)
    decided_by: Mapped[int | None] = mapped_column(Integer)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
