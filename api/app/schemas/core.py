from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)
    totp_code: str | None = None
    tenant_slug: str | None = None
    # entro con una contrasena temporal (Ajustes -> Usuarios -> Restablecer): la nueva va en el mismo login
    new_password: str | None = Field(None, max_length=200)


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


class TwoFactorStateOut(BaseModel):
    required: bool
    deadline: datetime | None = None
    blocked: bool = False


class MeOut(BaseModel):
    user: UserOut
    tenant: TenantOut
    role: str
    # Ajustes -> Usuarios -> Roles (reunion 21/10): nombre de un rol creado por el admin, y si el admin cambio los
    # permisos del rol (el portal deja de usar el menu fijo del rol y lo arma solo con los permisos)
    role_label: str | None = None
    role_customized: bool = False
    # con el rol cambiado o creado: el predefinido del que sale y sus permisos de fabrica. El portal conserva el menu
    # fijo de ese rol y solo suma las secciones de lo que el admin le agrego (nunca destapa lo que el menu ocultaba)
    role_base: str | None = None
    role_defaults: dict | None = None
    permissions: dict
    memberships: list[MembershipOut]
    # solo usuarios de una empresa aliada: {id, name, state, suspended_reason, features: {ganancias}}
    ally_company: dict | None = None
    # 2FA obligatorio para su rol (core/twofa.py): el portal avisa en la gracia y fuerza la configuracion al vencer
    twofa: TwoFactorStateOut | None = None


class TotpSetupOut(BaseModel):
    secret: str
    otpauth_uri: str


class TotpSetupIn(BaseModel):
    """Cambio de telefono con el 2FA activo: contrasena y codigo actual (o uno de recuperacion). Primera vez: vacio."""

    password: str | None = Field(None, max_length=200)
    code: str | None = Field(None, max_length=20)


class RecoveryCodesIn(BaseModel):
    """Codigos de recuperacion nuevos: contrasena y el codigo ACTUAL de la app (no uno de recuperacion: con uno de esos
    se usa "Cambiar de telefono", que al confirmar tambien entrega codigos nuevos)."""

    password: str = Field(max_length=200)
    code: str = Field("", max_length=20)


class TotpVerifyIn(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class RecoveryCodesOut(BaseModel):
    codes: list[str]


class PasswordOnlyIn(BaseModel):
    password: str = Field(max_length=200)


class TotpDisableIn(BaseModel):
    """Desactivar el 2FA (si el rol no lo exige): contrasena y el codigo actual de la app o uno de recuperacion, lo
    mismo que "Cambiar de telefono". Con la sesion y la contrasena solas no se apaga para configurarlo con otra app."""

    password: str = Field(max_length=200)
    code: str = Field("", max_length=20)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(min_length=10, max_length=200)


class ResetTokenIn(BaseModel):
    """El token viaja en el cuerpo (POST), nunca en la URL de la API: asi no queda en ningun log."""

    token: str = Field(max_length=200)


class ResetConfirmIn(BaseModel):
    token: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


class ResetRequestIn(BaseModel):
    email: EmailStr


class ExchangeRateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    date: date
    currency: str
    sell: Decimal
    buy: Decimal
    source: str


class FxTodayOut(ExchangeRateOut):
    """La tasa vigente con lo que el editor de documentos muestra: fuente legible y si esta vieja."""

    source_label: str
    stale: bool = False
    business_days_old: int = 0


class ExchangeRateIn(BaseModel):
    date: date
    currency: str = "USD"
    sell: Decimal = Field(gt=0)
    buy: Decimal = Field(gt=0)
    note: str | None = None
