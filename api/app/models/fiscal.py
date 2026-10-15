"""Hacienda (perfil del emisor, actividades, credenciales cifradas) y recepcion de comprobantes (buzon, correos,
mensajes receptor). Las credenciales nunca viven en tenant.settings: esa columna viaja en GET /settings."""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, LargeBinary, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, deferred, mapped_column

from ..core.db import Base
from .base import TenantMixin, TimestampMixin, utcnow


class FiscalProfile(TenantMixin, TimestampMixin, Base):
    """Datos del emisor que no estan en Tenant. Razon social y cedula siguen en Tenant.legal_name / Tenant.tax_id."""

    __tablename__ = "fiscal_profile"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_fiscal_profile_tenant"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    id_type: Mapped[str] = mapped_column(String(2), default="02")  # 01 fisica, 02 juridica, 03 DIMEX, 04 NITE
    commercial_name: Mapped[str | None] = mapped_column(String(160))
    email: Mapped[str | None] = mapped_column(String(200))
    phone_country: Mapped[str] = mapped_column(String(3), default="506")
    phone: Mapped[str | None] = mapped_column(String(20))
    province: Mapped[str | None] = mapped_column(String(1))
    canton: Mapped[str | None] = mapped_column(String(2))
    district: Mapped[str | None] = mapped_column(String(2))
    district_name: Mapped[str | None] = mapped_column(String(80))
    barrio: Mapped[str | None] = mapped_column(String(50))
    other_signs: Mapped[str | None] = mapped_column(String(250))
    environment: Mapped[str] = mapped_column(String(12), default="pruebas")  # pruebas | produccion (solo por el interruptor)
    production_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    production_enabled_by: Mapped[int | None] = mapped_column(Integer)
    production_instance: Mapped[str | None] = mapped_column(String(16))  # huella del servidor donde se activo
    consecutives_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutives_confirmed_by: Mapped[int | None] = mapped_column(Integer)


class FiscalActivity(TenantMixin, Base):
    __tablename__ = "fiscal_activity"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_fiscal_activity"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(6))
    description: Mapped[str | None] = mapped_column(String(200))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class FiscalCredential(TenantMixin, TimestampMixin, Base):
    """Credenciales de Hacienda por ambiente, cifradas con core/secretbox (AES-256-GCM, FISCAL_KEYS). Solo se escriben."""

    __tablename__ = "fiscal_credential"
    __table_args__ = (UniqueConstraint("tenant_id", "environment", name="uq_fiscal_credential_env"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(12))  # pruebas | produccion
    atv_user_enc: Mapped[str | None] = mapped_column(Text)
    atv_password_enc: Mapped[str | None] = mapped_column(Text)
    p12_enc: Mapped[str | None] = mapped_column(Text)
    p12_pin_enc: Mapped[str | None] = mapped_column(Text)
    key_id: Mapped[str | None] = mapped_column(String(20))
    cert_subject: Mapped[str | None] = mapped_column(String(300))
    cert_issuer: Mapped[str | None] = mapped_column(String(300))
    cert_serial: Mapped[str | None] = mapped_column(String(80))
    cert_not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cert_not_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cert_sha256: Mapped[str | None] = mapped_column(String(64))
    cert_key_bits: Mapped[int | None] = mapped_column(Integer)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_test_detail: Mapped[str | None] = mapped_column(String(300))
    updated_by: Mapped[int | None] = mapped_column(Integer)


class ReceptionMailbox(TenantMixin, Base):
    """Estado de lectura de un buzon (UIDVALIDITY + ultimo UID) y su arriendo para que dos lectores no se pisen."""

    __tablename__ = "reception_mailbox"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_reception_mailbox"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(200))  # env:usuario@host/carpeta | db:usuario@host/carpeta (nunca la contrasena)
    uidvalidity: Mapped[int | None] = mapped_column(BigInteger)
    last_uid: Mapped[int] = mapped_column(BigInteger, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_result: Mapped[dict | None] = mapped_column(JSON)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(64))


class ReceptionMessage(TenantMixin, Base):
    """Un correo (o una carga manual) y lo que se hizo con cada adjunto. El .eml crudo solo se guarda como evidencia
    cuando algo fallo (error o parcial): el buzon tambien recibe boletines y correos personales."""

    __tablename__ = "reception_message"
    __table_args__ = (UniqueConstraint("tenant_id", "mailbox_id", "uidvalidity", "uid", name="uq_reception_message_uid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    mailbox_id: Mapped[int | None] = mapped_column(ForeignKey("reception_mailbox.id", name="fk_reception_message_mailbox"))
    source: Mapped[str] = mapped_column(String(10))  # imap | manual | historico
    uidvalidity: Mapped[int | None] = mapped_column(BigInteger)
    uid: Mapped[int | None] = mapped_column(BigInteger)
    message_id: Mapped[str | None] = mapped_column(String(300))
    sender: Mapped[str | None] = mapped_column(String(300))
    subject: Mapped[str | None] = mapped_column(String(300))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    raw_eml: Mapped[bytes | None] = deferred(mapped_column(LargeBinary))
    status: Mapped[str] = mapped_column(String(16), default="procesado")  # procesado | sin_comprobantes | parcial | error
    summary: Mapped[list | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReceptionResponse(TenantMixin, Base):
    """Cada intento de mensaje receptor. Se firma una sola vez (D6): todo reintento manda exactamente esos bytes."""

    __tablename__ = "reception_response"
    __table_args__ = (
        UniqueConstraint("received_document_id", "attempt", name="uq_reception_response_attempt"),
        # con el modo: los T0x (simulado y pruebas) copian sucursal y terminal de su F0x, asi que T05 n.o N es el mismo
        # texto que F05 n.o N (el de Fygaro en el historico). Dentro de un mismo modo el consecutivo no se repite.
        UniqueConstraint("tenant_id", "mode", "consecutive", name="uq_reception_response_consecutive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    received_document_id: Mapped[int] = mapped_column(ForeignKey("received_document.id", name="fk_reception_response_document"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    message_code: Mapped[str] = mapped_column(String(1))  # 1 | 2 | 3
    iva_condition_code: Mapped[str | None] = mapped_column(String(2))
    activity_code: Mapped[str | None] = mapped_column(String(10))
    tax_total: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    total: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    credit_amount: Mapped[float | None] = mapped_column(Numeric(16, 5))
    applicable_amount: Mapped[float | None] = mapped_column(Numeric(16, 5))
    detail: Mapped[str | None] = mapped_column(String(160))
    consecutive: Mapped[str] = mapped_column(String(20))
    mode: Mapped[str] = mapped_column(String(12))  # simulado | pruebas | produccion
    emitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    xml_signed: Mapped[str | None] = mapped_column(Text)
    # por_enviar | enviando | enviado | procesando | aceptado | rechazado | error | simulado | historico
    status: Mapped[str] = mapped_column(String(12), default="por_enviar")
    provider_ref: Mapped[str | None] = mapped_column(String(200))
    hacienda_xml: Mapped[str | None] = mapped_column(Text)
    hacienda_detail: Mapped[str | None] = mapped_column(String(500))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(300))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

