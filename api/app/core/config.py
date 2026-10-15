"""Configuracion central (pydantic-settings). Todo secreto viene de variables de entorno."""

import base64
import binascii
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_fiscal_keys(raw: str | None) -> list[tuple[str, bytes]]:
    """FISCAL_KEYS = "kid:llave,kid:llave" (llave = base64url de 32 bytes). La primera cifra; todas descifran.
    Lanza ValueError con un mensaje claro (sin mostrar las llaves) si el formato no sirve."""
    out: list[tuple[str, bytes]] = []
    for part in [p.strip() for p in (raw or "").split(",") if p.strip()]:
        kid, sep, key = part.partition(":")
        kid = kid.strip()
        if not sep or not kid or not kid.isascii() or not kid.replace("_", "").replace("-", "").isalnum() or len(kid) > 20:
            raise ValueError("FISCAL_KEYS: cada llave va como kid:llave (kid alfanumérico de hasta 20 caracteres)")
        try:
            raw_key = base64.urlsafe_b64decode(key.strip() + "=" * (-len(key.strip()) % 4))
        except (binascii.Error, ValueError) as e:
            raise ValueError(f"FISCAL_KEYS: la llave {kid} no es base64url válido") from e
        if len(raw_key) != 32:
            raise ValueError(f"FISCAL_KEYS: la llave {kid} debe decodificar a 32 bytes")
        if any(k == kid for k, _ in out):
            raise ValueError(f"FISCAL_KEYS: el kid {kid} está repetido")
        out.append((kid, raw_key))
    return out


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Crimson API"
    env: str = Field("local", description="local | staging | production")
    debug: bool = False

    database_url: str = Field(
        "sqlite:///./crimson.db",
        description="Postgres en staging/produccion (postgresql+psycopg://...). SQLite solo para desarrollo rapido.",
    )
    redis_url: str = "redis://localhost:6379/0"

    # Auth
    jwt_secret: str = Field("solo-desarrollo-cambia-esto-en-.env-0123456789", min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    cookie_secure: bool = False  # True en staging/produccion (HTTPS)
    cookie_domain: str | None = None

    # CORS: origenes del portal (nunca "*")
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    # Sitios publicos (landing) que pueden llamar a /public/incidents. Aparte del portal para no tener que
    # reescribir CORS_ORIGINS: en Railway LANDING_ORIGINS='["https://crimsoncr.com","https://www.crimsoncr.com"]'
    landing_origins: list[str] = []
    # Empresa que recibe los reportes de la pagina web (slug). Vacio = la unica empresa activa, si hay una sola.
    # Nunca sale del cuerpo de la peticion: el que reporta no elige a que empresa le cae el ticket.
    public_incident_tenant: str | None = None

    # Enlaces publicos de pago
    public_base_url: str = "http://localhost:5173"
    payment_link_days: int = 30

    # Archivos privados (fotos de levantamientos, OT, proyectos, adjuntos contables, tickets): se sirven solo con
    # enlace firmado HMAC que vence. Sin MEDIA_URL_SECRET se deriva del JWT_SECRET (nunca se usa el JWT tal cual).
    media_url_secret: str | None = Field(None, min_length=32)
    media_url_minutes: int = 30  # vida del enlace que pide el portal para mostrar una foto
    media_email_url_days: int = 30  # informes que viajan por correo: el cliente abre el correo dias despues

    # Integraciones (vacias = adaptadores en modo manual/sandbox)
    # ONVO por variable de entorno = respaldo de una sola empresa; lo normal es Ajustes -> Pasarelas (cifrado por empresa)
    onvo_public_key: str | None = None
    onvo_secret_key: str | None = None
    onvo_webhook_secret: str | None = None
    # Empresa (slug) a la que aplican las llaves ONVO de entorno. Vacio = la unica empresa activa, si hay una sola.
    onvo_env_tenant: str | None = None
    # URL publica de la API para armar la URL del webhook que se registra en ONVO. Vacio = PUBLIC_BASE_URL + "/api"
    # (el nginx del portal pasa /api a la API sin tocar cuerpo ni headers).
    api_public_url: str | None = None
    einvoice_provider: str = "none"  # none | sandbox | alanube | gti ("hacienda" solo lo pone Ajustes -> Hacienda)

    # ---------- Hacienda (factura electronica directa) y recepcion ----------
    # Llaves de cifrado de las credenciales fiscales (kid:base64url32, separadas por coma; la primera cifra).
    fiscal_keys: SecretStr | None = None
    # Segunda llave para produccion: solo cuenta con ENV=production y la huella de instancia guardada al activar.
    hacienda_production_allowed: bool = False
    hacienda_proveedor_sistemas: str | None = None  # cedula de ProveedorSistemas; vacio = la de la empresa
    hacienda_env_tenant: str | None = None  # slug de la empresa de las credenciales de entorno; vacio = la unica activa
    hacienda_environment: Literal["pruebas", "produccion"] = "pruebas"  # ambiente de las credenciales de entorno
    hacienda_username: SecretStr | None = None
    hacienda_password: SecretStr | None = None
    hacienda_p12_b64: SecretStr | None = None
    hacienda_p12_pin: SecretStr | None = None
    railway_environment_id: str | None = None  # lo pone Railway; parte de la huella de instancia
    # Buzon de recepcion de facturas (IMAP). Sin host, usuario o contrasena: buzon de entorno apagado.
    reception_imap_host: str | None = None
    reception_imap_port: int = 993
    reception_imap_user: str | None = None
    reception_imap_password: SecretStr | None = None
    reception_imap_folder: str = "INBOX"
    reception_imap_tenant: str | None = None
    reception_imap_since_days: int = 30

    # Arranque inicial de entornos compartidos (ver app/bootstrap.py): invitacion de admin, nunca contrasena
    bootstrap_admin_email: str | None = None
    bootstrap_demo: bool = False
    # Cuentas de prueba: "correo:rol,correo:rol" (p. ej. "vendedor@ejemplo.com:ventas"). Se invita una vez por correo.
    bootstrap_invites: str | None = None

    @field_validator("database_url")
    @classmethod
    def _psycopg3(cls, v: str) -> str:
        # Railway/Heroku entregan postgres:// o postgresql://; SQLAlchemy necesita el driver psycopg 3 explicito
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix) :]
        return v

    @field_validator("einvoice_provider")
    @classmethod
    def _provider(cls, v: str) -> str:
        # "hacienda" solo lo escriben Ajustes -> Hacienda y el interruptor de produccion (nunca una variable)
        if v not in ("none", "sandbox", "alanube", "gti"):
            raise ValueError("EINVOICE_PROVIDER admite none | sandbox | alanube | gti")
        return v

    @field_validator("fiscal_keys")
    @classmethod
    def _fiscal_keys(cls, v: SecretStr | None) -> SecretStr | None:
        if v is not None and v.get_secret_value().strip():
            parse_fiscal_keys(v.get_secret_value())
        return v

    @model_validator(mode="after")
    def _no_dev_secret_outside_local(self):
        if self.env != "local" and self.jwt_secret.startswith("solo-desarrollo"):
            raise ValueError("JWT_SECRET de desarrollo en un entorno compartido: definir uno propio (>= 32 caracteres)")
        if self.env != "local" and self.database_url.startswith("sqlite"):
            raise ValueError("Entornos compartidos requieren Postgres (DATABASE_URL)")
        return self

    @property
    def is_prod(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
