from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CustomerIn(BaseModel):
    id_type: str = "fisica"
    id_number: str | None = None
    name: str = Field(min_length=1, max_length=200)
    email: str | None = None
    phone: str | None = None
    whatsapp: str | None = None
    currency: str = "CRC"
    address: dict | None = None
    exemption: dict | None = None
    notes: str | None = None
    active: bool = True


class CustomerOut(CustomerIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class TaxIn(BaseModel):
    name: str
    code: str = "01"
    rate_code: str = "08"
    rate: float = 13
    active: bool = True


class TaxOut(TaxIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class CategoryIn(BaseModel):
    name: str
    description: str | None = None
    parent_id: int | None = None
    show_on_web: bool = True


class CategoryOut(CategoryIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=60)
    item_type: str = "producto"
    price: float = Field(0, ge=0)
    currency: str = "CRC"
    weight_kg: float | None = None
    show_on_web: bool = False
    description_invoice: str | None = None
    description_store: str | None = None
    images: list = Field(default_factory=list)
    supplier_id: int | None = None
    registration_number: str | None = None
    cabys_code: str | None = Field(None, max_length=13)
    cabys_description: str | None = None
    tariff_code: str | None = None
    unit: str = "Unid"
    min_stock: int = 0
    category_id: int | None = None
    tax_ids: list[int] = Field(default_factory=list)
    active: bool = True


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    code: str
    item_type: str
    price: float
    currency: str
    weight_kg: float | None
    show_on_web: bool
    description_invoice: str | None
    description_store: str | None
    images: list
    supplier_id: int | None
    registration_number: str | None
    cabys_code: str | None
    cabys_description: str | None
    tariff_code: str | None
    unit: str
    min_stock: int
    category_id: int | None
    active: bool
    tax_ids: list[int] = []
    tax_rate: float | None = None


class Page(BaseModel):
    items: list
    next_cursor: int | None = None
    total: int | None = None
