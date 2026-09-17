from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginIn(BaseModel):
    email: EmailStr
    password: str
    totp_code: str | None = None
    tenant_slug: str | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    tenant: TenantOut
    user: UserOut
    role: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    full_name: str
    totp_enabled: bool


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    slug: str
    name: str
    legal_name: str | None
    tax_id: str | None
    default_currency: str
    logo_url: str | None
    plan: str


class TenantUpdate(BaseModel):
    name: str | None = None
    legal_name: str | None = None
    tax_id: str | None = None
    sector: str | None = None
    language: str | None = None
    default_currency: str | None = Field(None, pattern="^[A-Z]{3}$")
    logo_url: str | None = None
    settings: dict | None = None


class MembershipOut(BaseModel):
    tenant: TenantOut
    role: str


class MeOut(BaseModel):
    user: UserOut
    tenant: TenantOut
    role: str
    permissions: dict
    memberships: list[MembershipOut]


class TotpSetupOut(BaseModel):
    secret: str
    otpauth_uri: str


class TotpVerifyIn(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10)


class ExchangeRateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    date: date
    currency: str
    sell: Decimal
    buy: Decimal
    source: str


class ExchangeRateIn(BaseModel):
    date: date
    currency: str = "USD"
    sell: Decimal = Field(gt=0)
    buy: Decimal = Field(gt=0)
    note: str | None = None
