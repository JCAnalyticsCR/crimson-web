from __future__ import annotations

from datetime import date

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
    # insignia "Empresa aliada": la empresa aliada que se factura a este cliente (solo con aliados.ver)
    ally_company: dict | None = None


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
    cost: float | None = Field(None, ge=0)
    cost_currency: str = "USD"
    margin_pct: float | None = Field(None, ge=0, le=95)
    brand: str | None = None
    model: str | None = None
    supplier_sku: str | None = None
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
    warehouse_id: int | None = None  # bodega de origen por defecto
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
    cost: float | None = None
    cost_currency: str = "USD"
    margin_pct: float | None = None
    brand: str | None = None
    model: str | None = None
    supplier_sku: str | None = None
    supplier_stock: int | None = None
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
    warehouse_id: int | None = None
    active: bool
    tax_ids: list[int] = []
    tax_rate: float | None = None
    # de donde sale el costo y el margen real (solo roles que ven costos; ver supplier_catalog.cost_info)
    cost_source: str | None = None  # proveedor | propio
    cost_supplier: str | None = None
    cost_date: date | None = None
    cost_active: bool | None = None  # False = el proveedor ya no lo trae en la lista vigente
    cost_crc: float | None = None
    margin_real_pct: float | None = None
    margin_provisional: bool | None = None  # sin costo: es el margen objetivo, no el real
    supplier_offers: int | None = None  # ofertas de proveedor vigentes vinculadas
    # precio GENERAL de aliado (services/ally_pricing.py): solo quien ve costos (deja deducir el costo: 105 / 1,05)
    ally_price: float | None = None
    ally_price_source: str | None = None
    # guardian de margen (plan 1.12): fuera de venta en la tienda por quedar bajo el minimo. Solo quien ve costos
    margin_hold: bool | None = None
    # disponibilidad de la funcion unica (services/availability, audiencia interno): propio, proveedor y fecha
    availability: dict | None = None


class Page(BaseModel):
    items: list
    next_cursor: int | None = None
    total: int | None = None
