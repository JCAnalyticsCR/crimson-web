from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class LineInSchema(BaseModel):
    product_id: int | None = None
    code: str | None = None
    name: str = ""
    description: str | None = None
    cabys_code: str | None = None
    unit: str | None = "Unid"
    quantity: Decimal = Field(Decimal(1), gt=0)
    unit_price: Decimal | None = Field(None, ge=0)
    discount_type: str = "percent"
    discount_value: Decimal = Field(Decimal(0), ge=0)
    tax_rate: Decimal | None = None


class DocumentIn(BaseModel):
    customer_id: int | None = None
    currency: str = Field("CRC", pattern="^[A-Z]{3}$")
    fx_sell: Decimal | None = None
    fx_buy: Decimal | None = None
    issue_date: date | None = None
    due_date: date | None = None
    valid_days: int | None = None
    discount_type: str = "percent"
    discount_value: Decimal = Field(Decimal(0), ge=0)
    internal_notes: str | None = None
    external_notes: str | None = None
    external_order: str | None = None
    activity_code: str | None = None
    medical_exemption_card: bool = False
    sale_condition: str | None = None
    credit_days: int | None = None
    payment_method: str | None = None
    lines: list[LineInSchema] = Field(default_factory=list)


class LineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    position: int
    product_id: int | None
    code: str | None
    name: str
    description: str | None
    cabys_code: str | None
    unit: str
    quantity: Decimal
    unit_price: Decimal
    discount_type: str
    discount_value: Decimal
    tax_rate: Decimal
    subtotal: Decimal
    tax_amount: Decimal
    total: Decimal


class DocBaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    number: str
    customer_id: int | None
    customer_name: str | None = None
    currency: str
    fx_sell: Decimal
    fx_buy: Decimal
    issue_date: date
    due_date: date | None
    discount_type: str
    discount_value: Decimal
    subtotal: Decimal
    discount_total: Decimal
    tax_total: Decimal
    total: Decimal
    internal_notes: str | None
    external_notes: str | None
    external_order: str | None
    activity_code: str | None
    medical_exemption_card: bool
    status: str
    lines: list[LineOut] = []


class QuoteOut(DocBaseOut):
    converted_invoice_id: int | None = None


class PaymentIn(BaseModel):
    method: str = "efectivo"  # efectivo | tarjeta | sinpe | transferencia | onvo | paypal
    kind: str = "captura"  # autorizacion | captura | devolucion | reembolso | reautorizacion | void
    currency: str | None = None
    amount: Decimal = Field(gt=0)
    tip: Decimal | None = Decimal(0)
    bank_account_id: int | None = None
    external_ref: str | None = None
    paid_at: date | None = None
    notify_customer: bool = False
    notes: str | None = None


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    method: str
    kind: str
    currency: str
    amount: Decimal
    tip: Decimal
    bank_account_id: int | None
    external_ref: str | None
    provider: str
    paid_at: date
    status: str


class InvoiceOut(DocBaseOut):
    doc_type: str
    consecutive: str | None
    clave: str | None
    sale_condition: str
    credit_days: int
    payment_method: str
    balance: Decimal
    quote_id: int | None
    einvoice_status: str
    payments: list[PaymentOut] = []


class PaymentLinkOut(BaseModel):
    url: str
    whatsapp_url: str
    expires_at: str
    opened_count: int


class DocListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    number: str
    customer_name: str | None = None
    currency: str
    total: Decimal
    balance: Decimal | None = None
    status: str
    issue_date: date
    due_date: date | None
